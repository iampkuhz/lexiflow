"""Planning Catalog 的静态一致性检查。

依次核对 ID、owner、dependency 类型与 producer、DAG、阶段前置条件及 handoff 投影。
这是 Repository 维护能力，不负责启动产品测试，也不授予 Task 执行权限。"""

import re
from collections import deque
from pathlib import Path
from typing import Any

import yaml

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
PHASE_RE = re.compile(r"^P\d+$")

ID_PATTERNS = {
    "program": re.compile(r"^LF-PRG-\d{3}$"),
    "workstream": re.compile(r"^LF-WS-[A-Z]+$"),
    "epic": re.compile(r"^LF-EP-[A-Z]+-\d{3}$"),
    "capability": re.compile(r"^LF-CP-[A-Z]+-\d{3}$"),
    "task": re.compile(r"^LF-TSK-[A-Z]+-\d{4}$"),
    "gate": re.compile(r"^G\d+$"),
}

CANONICAL_ID_POLICY = {
    "program": "LF-PRG-NNN",
    "workstream": "LF-WS-{DOMAIN}",
    "epic": "LF-EP-{DOMAIN}-NNN",
    "capability": "LF-CP-{DOMAIN}-NNN",
    "task": "LF-TSK-{DOMAIN}-NNNN",
}

ALLOWED_DEP_TYPES = {"hard", "soft", "contract"}

COMMON_REQUIRED_FIELDS = {
    "task_id",
    "type",
    "required_task_version",
    "required_change_version",
}


def _load_canonical_from_policy(policy: dict | None) -> dict[str, Any]:
    """从 policy 唯一真源提取 handoff contract。

    返回 caller/result 字段、runner 身份、adapter 约束及 schema；真源不可用时返回 None，
    由调用者拒绝继续，不能把缺失 contract 当作空 contract。"""
    if not isinstance(policy, dict):
        return None
    sp = policy.get("subagent_protocol", {})
    if not isinstance(sp, dict):
        return None
    caller = sp.get("caller_required_input")
    result = sp.get("result_required_output")
    identity = sp.get("runner_bound_identity")
    adapter = sp.get("current_runner_adapter", {})
    schema = sp.get("caller_field_schema")
    if not isinstance(caller, list) or not isinstance(result, list):
        return None
    adapter_flags = {
        "target_schema_enforced": adapter.get("target_schema_enforced"),
        "runtime_identity_enforced": adapter.get("runtime_identity_enforced"),
        "result_fields_required_in_prompt": adapter.get(
            "result_fields_required_in_prompt"
        ),
        "agent_profile_cli_binding": adapter.get("agent_profile_cli_binding"),
        "harness_manifest_preflight": adapter.get("harness_manifest_preflight"),
        "catalog_package_preflight": adapter.get("catalog_package_preflight"),
        "catalog_estimate_rule": adapter.get("catalog_estimate_rule"),
        "structured_result_required": adapter.get("structured_result_required"),
    }
    adapter_metadata = {
        "implementation_status": adapter.get("implementation_status"),
        "conformance_test": adapter.get("conformance_test"),
        "owner_task": adapter.get("owner_task"),
        "owner_task_version": adapter.get("owner_task_version"),
        "owner_change_version": adapter.get("owner_change_version"),
    }
    return {
        "caller_fields": list(caller),
        "result_fields": list(result),
        "runner_identity": identity,
        "adapter_flags": adapter_flags,
        "adapter_metadata": adapter_metadata,
        "caller_field_schema": schema,
        "adapter_enforced_fields": list(adapter.get("enforced_caller_fields", [])),
    }


EXCLUDED_PATH_PATTERNS = [
    ".git/**",
    ".git",
    ".idea/**",
    ".idea",
    ".vscode/**",
    ".vscode",
    "**/.gradle/**",
    "**/.gradle",
    "**/build/**",
    "**/build",
    "tmp/**",
    "tmp",
    ".local/**",
    ".local",
    "data/**",
    "data",
    "secrets/**",
    "secrets",
    "**/__pycache__/**",
    "**/__pycache__",
    "**/*.pyc",
    ".pytest_cache/**",
    ".pytest_cache",
    ".ruff_cache/**",
    ".ruff_cache",
    ".agents/**",
    ".agents",
    ".qoder/**",
    ".qoder",
    ".codex/**",
    ".codex",
    ".githooks/**",
    ".githooks",
    "AGENTS.md",
    "README.md",
    "requirements*.txt",
    "extension/node_modules/**",
    "extension/package-lock.json",
]


def _phase_number(task):
    return int(task["phase"][1:])


def _is_semver(value):
    return (
        isinstance(value, (str, int, float)) and SEMVER_RE.match(str(value)) is not None
    )


def _path_specificity(pattern):
    segments = _path_pattern(pattern)
    if segments is None:
        return (-1, -1)
    literal_prefix_depth = 0
    for segment in segments:
        if segment in ("*", "**"):
            break
        literal_prefix_depth += 1
    if segments[-1:] == ("**",):
        pattern_rank = 0
    elif "*" in segments:
        pattern_rank = 1
    else:
        pattern_rank = 2
    return (literal_prefix_depth, pattern_rank)


def _path_matches(pattern, file_path):
    pattern_segments = _path_pattern(pattern)
    path_segments = _literal_path(file_path)
    if pattern_segments is None or path_segments is None:
        return False

    subtree = pattern_segments[-1:] == ("**",)
    prefix = pattern_segments[:-1] if subtree else pattern_segments
    if subtree:
        if len(path_segments) <= len(prefix):
            return False
    elif len(path_segments) != len(prefix):
        return False

    return all(
        expected == "*" or expected == actual
        for expected, actual in zip(prefix, path_segments)
    )


def _path_pattern(pattern):
    """解析 Catalog 的整段 glob；通配符仅允许出现在末段，不沿用任意 shell glob 语义。"""
    if not isinstance(pattern, str) or not pattern:
        return None
    if pattern.startswith("/") or "\\" in pattern:
        return None
    segments = pattern.split("/")
    if any(
        not segment
        or segment in (".", "..")
        or any(char in segment for char in "?[]{}")
        or segment.startswith("!")
        or ("*" in segment and segment not in ("*", "**"))
        for segment in segments
    ):
        return None
    if any(segment in ("*", "**") for segment in segments[:-1]):
        return None
    return tuple(segments)


def _literal_path(file_path):
    if not isinstance(file_path, str) or not file_path:
        return None
    if file_path.startswith("/") or "\\" in file_path:
        return None
    segments = file_path.split("/")
    if any(
        not segment or segment in (".", "..") or "*" in segment for segment in segments
    ):
        return None
    return tuple(segments)


def _segments_compatible(left, right):
    return left == right or left == "*" or right == "*"


def _path_patterns_overlap(left, right):
    """判断两个整段 glob 是否可能覆盖同一路径，用于发现 ownership 冲突。"""
    left_parsed = _path_pattern(left)
    right_parsed = _path_pattern(right)
    if left_parsed is None or right_parsed is None:
        return left == right

    left_subtree = left_parsed[-1:] == ("**",)
    right_subtree = right_parsed[-1:] == ("**",)
    left_prefix = left_parsed[:-1] if left_subtree else left_parsed
    right_prefix = right_parsed[:-1] if right_subtree else right_parsed

    common_length = min(len(left_prefix), len(right_prefix))
    if any(
        not _segments_compatible(left_prefix[index], right_prefix[index])
        for index in range(common_length)
    ):
        return False

    if not left_subtree and not right_subtree:
        return len(left_prefix) == len(right_prefix)
    if left_subtree and right_subtree:
        return True
    if left_subtree:
        return len(right_prefix) > len(left_prefix)
    return len(left_prefix) > len(right_prefix)


def _is_excluded(file_path):
    path_segments = _literal_path(file_path)
    if path_segments is not None:
        # Finder 元数据已由 .gitignore 排除；仅匹配文件名，不扩展 Catalog glob 语法。
        if path_segments[-1] == ".DS_Store":
            return True
        if any(
            segment in {"__pycache__", ".gradle", "build"} for segment in path_segments
        ) or file_path.endswith(".pyc"):
            return True
    for pat in EXCLUDED_PATH_PATTERNS:
        if _path_matches(pat, file_path):
            return True
    return False


class PlanningValidator:
    """按共享 contract 核对 planning/workstreams.yaml，累积可定位的错误。"""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self._load_errors = []
        self._structure_errors = []
        self._check_failures = []
        self.ws = None
        self.template = None
        self.policy = None
        self.runtime = None
        self.module_checks = None
        self.tasks = {}
        self.workstreams = {}
        self.gates = []
        self.path_scopes = []
        self.phase_entry_tasks = {}
        self.errors = []

    @classmethod
    def from_data(
        cls,
        ws_data,
        template_data=None,
        policy_data=None,
        runtime_data=None,
        module_checks_data=None,
    ):
        """由已加载的规划数据构造校验器，供隔离测试复用。"""
        obj = cls.__new__(cls)
        obj.root = Path(".")
        obj._load_errors = []
        obj._structure_errors = []
        obj._check_failures = []
        obj.ws = ws_data
        obj.template = template_data
        obj.policy = policy_data
        obj.runtime = runtime_data
        obj.module_checks = module_checks_data
        obj.tasks = {}
        obj.workstreams = {}
        obj.gates = []
        obj.path_scopes = []
        obj.phase_entry_tasks = {}
        obj.errors = []
        obj._build_catalog()
        return obj

    def _load_yaml(self, relative_path):
        target = self.root / relative_path
        if not target.exists():
            self._load_errors.append(f"Required file not found: {relative_path}")
            return None
        try:
            with open(target, "r", encoding="utf-8") as fh:
                return yaml.safe_load(fh)
        except Exception as exc:
            self._load_errors.append(f"Cannot parse {relative_path}: {exc}")
            return None

    def load_sources(self):
        """读取 Catalog 与共享策略真源，缺项不能当作空规划。"""
        self.ws = self._load_yaml("planning/workstreams.yaml")
        self.template = self._load_yaml("planning/task-template.yaml")
        self.policy = self._load_yaml("harness/agent-policy.manifest.yaml")
        self.runtime = self._load_yaml("harness/agent-runtime.manifest.yaml")
        self.module_checks = self._load_yaml("harness/module-checks.yaml")
        if self.ws:
            self._build_catalog()

    def _build_catalog(self):
        """先校验并展平 WorkStream → Epic → Capability → Task；结构错误不流入后续图检查。"""
        self.tasks = {}
        self.workstreams = {}
        self.gates = []
        self.path_scopes = []
        self.phase_entry_tasks = {}
        self._structure_errors = []
        self._check_failures = []

        if not isinstance(self.ws, dict):
            self._structure_errors.append(
                "invalid-structure: workstreams catalog is not a dict"
            )
            return

        ws_raw = self.ws.get("workstreams")
        if ws_raw is not None and not isinstance(ws_raw, list):
            self._structure_errors.append(
                "invalid-structure: workstreams is not a list"
            )
            return

        gates_raw = self.ws.get("phase_gates")
        if gates_raw is not None and not isinstance(gates_raw, list):
            self._structure_errors.append(
                "invalid-structure: phase_gates is not a list"
            )
            return

        path_ownership = self.ws.get("path_ownership", {})
        if not isinstance(path_ownership, dict):
            self._structure_errors.append(
                "invalid-structure: path_ownership is not a map"
            )
            path_ownership = {}
        scopes_raw = path_ownership.get("scopes")
        if scopes_raw is not None and not isinstance(scopes_raw, list):
            self._structure_errors.append(
                "invalid-structure: path_ownership.scopes is not a list"
            )
            scopes_raw = []

        phase_entry_validation = self.ws.get("phase_entry_validation", {})
        if not isinstance(phase_entry_validation, dict):
            self._structure_errors.append(
                "invalid-structure: phase_entry_validation is not a map"
            )
            phase_entry_validation = {}
        entry_tasks_raw = phase_entry_validation.get("entry_tasks", {})
        if not isinstance(entry_tasks_raw, dict):
            self._structure_errors.append(
                "invalid-structure: phase_entry_validation.entry_tasks is not a map"
            )
            entry_tasks_raw = {}

        self.gates = list(gates_raw or [])
        self.path_scopes = list(scopes_raw or [])
        self.phase_entry_tasks = {}
        for phase_key, task_ids in entry_tasks_raw.items():
            if not isinstance(task_ids, list):
                self._structure_errors.append(
                    "invalid-structure: phase_entry_validation.entry_tasks"
                    f".{phase_key} is not a list"
                )
                continue
            self.phase_entry_tasks[phase_key] = list(task_ids)

        seen_ws_ids = set()
        seen_epic_ids = set()
        seen_cap_ids = set()
        for ws_index, ws in enumerate(ws_raw or []):
            if not isinstance(ws, dict):
                self._structure_errors.append(
                    f"invalid-structure: workstreams[{ws_index}] is not a map"
                )
                continue
            ws_id = ws.get("id", "")
            if ws_id in seen_ws_ids:
                self.errors.append(f"duplicate-workstream-id: {ws_id}")
            seen_ws_ids.add(ws_id)
            self.workstreams[ws_id] = ws

            epics_raw = ws.get("epics")
            if epics_raw is not None and not isinstance(epics_raw, list):
                self._structure_errors.append(
                    f"invalid-structure: workstream {ws_id!r} epics is not a list"
                )
                continue

            for epic_index, epic in enumerate(epics_raw or []):
                if not isinstance(epic, dict):
                    self._structure_errors.append(
                        f"invalid-structure: workstream {ws_id!r} epics[{epic_index}] "
                        "is not a map"
                    )
                    continue
                eid = epic.get("id", "")
                if eid in seen_epic_ids:
                    self.errors.append(f"duplicate-epic-id: {eid}")
                seen_epic_ids.add(eid)

                capabilities_raw = epic.get("capabilities")
                if capabilities_raw is not None and not isinstance(
                    capabilities_raw, list
                ):
                    self._structure_errors.append(
                        f"invalid-structure: epic {eid!r} capabilities is not a list"
                    )
                    continue

                for cap_index, cap in enumerate(capabilities_raw or []):
                    if not isinstance(cap, dict):
                        self._structure_errors.append(
                            f"invalid-structure: epic {eid!r} capabilities[{cap_index}] "
                            "is not a map"
                        )
                        continue
                    cid = cap.get("id", "")
                    if cid in seen_cap_ids:
                        self.errors.append(f"duplicate-capability-id: {cid}")
                    seen_cap_ids.add(cid)

                    cap_phase = cap.get("phase", "P1")
                    if not isinstance(cap_phase, str) or not PHASE_RE.match(cap_phase):
                        self.errors.append(
                            f"invalid-phase: capability {cid} phase={cap_phase!r}"
                        )

                    seed_tasks_raw = cap.get("seed_tasks")
                    if seed_tasks_raw is not None and not isinstance(
                        seed_tasks_raw, list
                    ):
                        self._structure_errors.append(
                            f"invalid-structure: capability {cid!r} seed_tasks is not a list"
                        )
                        continue

                    for task_index, task in enumerate(seed_tasks_raw or []):
                        if not isinstance(task, dict):
                            self._structure_errors.append(
                                f"invalid-structure: capability {cid!r} "
                                f"seed_tasks[{task_index}] is not a map"
                            )
                            continue
                        task_id = task.get("id", "")
                        if task_id in self.tasks:
                            self.errors.append(f"duplicate-task-id: {task_id}")
                        depends_on = task.get("depends_on")
                        if depends_on is not None and not isinstance(depends_on, list):
                            self._structure_errors.append(
                                f"invalid-structure: task {task_id!r} depends_on is not a list"
                            )
                            depends_on = []
                        produced_contracts = task.get("produced_contracts")
                        if produced_contracts is not None and not isinstance(
                            produced_contracts, list
                        ):
                            self._structure_errors.append(
                                f"invalid-structure: task {task_id!r} produced_contracts "
                                "is not a list"
                            )
                            produced_contracts = []
                        elif isinstance(produced_contracts, list):
                            for contract_index, contract in enumerate(
                                produced_contracts
                            ):
                                if not isinstance(contract, dict):
                                    self._structure_errors.append(
                                        f"invalid-structure: task {task_id!r} "
                                        f"produced_contracts[{contract_index}] is not a map"
                                    )
                        task_phase = task.get("phase", cap_phase)
                        if not isinstance(task_phase, str) or not PHASE_RE.match(
                            task_phase
                        ):
                            self.errors.append(
                                f"invalid-phase: task {task_id} phase={task_phase!r}"
                            )
                        self.tasks[task_id] = {
                            "id": task_id,
                            "title": task.get("title", ""),
                            "phase": task_phase,
                            "priority": task.get("priority", "P1"),
                            "depends_on": depends_on or [],
                            "task_version": task.get("task_version"),
                            "change_version": task.get("change_version"),
                            "workstream_id": ws_id,
                            "epic_id": eid,
                            "capability_id": cid,
                            "phase_entry_prerequisite": task.get(
                                "phase_entry_prerequisite"
                            ),
                            "produced_contracts": produced_contracts or [],
                            "required_check_ids": task.get("required_check_ids"),
                        }

        seen_gate_ids = set()
        valid_gates = []
        for gate_index, gate in enumerate(self.gates):
            if not isinstance(gate, dict):
                self._structure_errors.append(
                    f"invalid-structure: phase_gates[{gate_index}] is not a map"
                )
                continue
            gate_entry_tasks = gate.get("entry_tasks")
            if gate_entry_tasks is not None and not isinstance(gate_entry_tasks, list):
                self._structure_errors.append(
                    f"invalid-structure: gate {gate.get('id', '')!r} entry_tasks is not a list"
                )
                continue
            entry_requires = gate.get("entry_requires")
            if entry_requires is not None and not isinstance(entry_requires, dict):
                self._structure_errors.append(
                    f"invalid-structure: gate {gate.get('id', '')!r} entry_requires "
                    "is not a map or null"
                )
                continue
            gid = gate.get("id", "")
            if gid in seen_gate_ids:
                self.errors.append(f"duplicate-gate-id: {gid}")
            seen_gate_ids.add(gid)
            valid_gates.append(gate)
        self.gates = valid_gates

        valid_scopes = []
        for scope_index, scope in enumerate(self.path_scopes):
            if not isinstance(scope, dict):
                self._structure_errors.append(
                    f"invalid-structure: path_ownership.scopes[{scope_index}] is not a map"
                )
                continue
            proposed_paths = scope.get("proposed_paths")
            if proposed_paths is not None and not isinstance(proposed_paths, list):
                self._structure_errors.append(
                    f"invalid-structure: ownership scope {scope.get('scope', '')!r} "
                    "proposed_paths is not a list"
                )
                continue
            valid_scopes.append(scope)
        self.path_scopes = valid_scopes

        handoff_contracts = []
        orchestration_policy = self.ws.get("orchestration_policy")
        if orchestration_policy is not None and not isinstance(
            orchestration_policy, dict
        ):
            self._structure_errors.append(
                "invalid-structure: orchestration_policy is not a map"
            )
        elif isinstance(orchestration_policy, dict):
            handoff_contracts.append(
                ("workstreams", orchestration_policy.get("handoff_contract"))
            )

        for source_name, source, contract_key in (
            ("template", self.template, "handoff_contract"),
            ("policy", self.policy, "subagent_protocol"),
            ("runtime", self.runtime, "subagent_protocol"),
        ):
            if source is None:
                continue
            if not isinstance(source, dict):
                self._structure_errors.append(
                    f"invalid-structure: {source_name} source is not a map"
                )
                continue
            handoff_contracts.append((source_name, source.get(contract_key)))

        for source_name, contract in handoff_contracts:
            if contract is None:
                continue
            if not isinstance(contract, dict):
                self._structure_errors.append(
                    f"invalid-structure: {source_name} handoff contract is not a map"
                )
                continue
            for field in ("current_runner_adapter", "caller_field_schema"):
                value = contract.get(field)
                if value is not None and not isinstance(value, dict):
                    self._structure_errors.append(
                        f"invalid-structure: {source_name} handoff {field} is not a map"
                    )

    def _check(self, fn):
        """隔离单项 checker 的异常并记录失败，避免一个异常吞掉完整检查清单。"""
        try:
            fn()
        except Exception as exc:
            failure = f"check-exception: {fn.__name__}: {exc}"
            self.errors.append(failure)
            self._check_failures.append(failure)

    def check_all_entity_ids(self):
        """核对各层 ID 格式及唯一性；名称说明不能代替稳定 ID。"""
        id_policy = self.ws.get("id_policy")
        if not isinstance(id_policy, dict):
            self.errors.append("invalid-id-policy: id_policy must be a map")
        else:
            for entity, expected in CANONICAL_ID_POLICY.items():
                actual = id_policy.get(entity)
                if actual != expected:
                    self.errors.append(
                        f"invalid-id-policy: {entity}={actual!r} expected {expected!r}"
                    )

        program = self.ws.get("program") or {}
        pid = program.get("id", "")
        if not ID_PATTERNS["program"].match(str(pid)):
            self.errors.append(f"invalid-program-id: {pid!r}")

        domain_codes = set((self.ws.get("domain_codes") or {}).keys())

        for ws_id, ws in self.workstreams.items():
            if not ID_PATTERNS["workstream"].match(str(ws_id)):
                self.errors.append(f"invalid-workstream-id: {ws_id!r}")
            code = ws.get("code", "")
            if code and code not in domain_codes:
                self.errors.append(f"unknown-domain-code: {ws_id} code={code!r}")
            for epic in ws.get("epics") or []:
                eid = epic.get("id", "")
                if not ID_PATTERNS["epic"].match(str(eid)):
                    self.errors.append(f"invalid-epic-id: {eid!r}")
                for cap in epic.get("capabilities") or []:
                    cid = cap.get("id", "")
                    if not ID_PATTERNS["capability"].match(str(cid)):
                        self.errors.append(f"invalid-capability-id: {cid!r}")

        for tid in self.tasks:
            if not ID_PATTERNS["task"].match(str(tid)):
                self.errors.append(f"invalid-task-id-format: {tid}")

        for gate in self.gates:
            gid = gate.get("id", "")
            if not ID_PATTERNS["gate"].match(str(gid)):
                self.errors.append(f"invalid-gate-id: {gid!r}")
            gate_phase = gate.get("phase")
            if not isinstance(gate_phase, str) or not PHASE_RE.match(gate_phase):
                self.errors.append(f"invalid-phase: gate {gid} phase={gate_phase!r}")

        for scope in self.path_scopes:
            sid = scope.get("scope", "")
            if not sid or not isinstance(sid, str):
                self.errors.append("invalid-scope-id: empty or missing scope name")
            elif not re.match(r"^[a-z][a-z0-9-]*(\.[a-z][a-z0-9-]*)*$", sid):
                self.errors.append(f"invalid-scope-id: {sid!r}")

    def check_versions(self):
        """核对 Task 正整数版本与 change SemVer，供 dependency 精确绑定。"""
        for tid, task in self.tasks.items():
            tv = task["task_version"]
            if not isinstance(tv, int) or tv < 1:
                self.errors.append(f"invalid-task-version: {tid} task_version={tv!r}")
            cv = task["change_version"]
            if not _is_semver(cv):
                self.errors.append(
                    f"invalid-change-version: {tid} change_version={cv!r}"
                )

    def check_required_check_ids(self):
        """核对 Task 引用的 Check 确实在 Registry 声明，不执行这些 Check。"""
        checks = (
            self.module_checks.get("checks")
            if isinstance(self.module_checks, dict)
            else None
        )
        if not isinstance(checks, list):
            self.errors.append(
                "required-check-registry: harness/module-checks.yaml checks missing"
            )
            return
        declared = {
            item.get("check_id")
            for item in checks
            if isinstance(item, dict) and item.get("scope") == "repository-baseline"
        }
        for tid, task in self.tasks.items():
            required = task.get("required_check_ids")
            if not isinstance(required, list) or not required:
                self.errors.append(f"required-check-ids-missing: {tid}")
                continue
            if len(required) != len(set(required)) or not all(
                isinstance(v, str) and v for v in required
            ):
                self.errors.append(f"required-check-ids-invalid: {tid}")
                continue
            unknown = sorted(set(required) - declared)
            if unknown:
                self.errors.append(f"required-check-ids-unknown: {tid} -> {unknown}")

    def check_dependency_types(self):
        """按 hard、soft、contract 分别核对边字段，不把建议顺序提升为强依赖。"""
        for tid, task in self.tasks.items():
            for idx, dep in enumerate(task["depends_on"]):
                if not isinstance(dep, dict):
                    self.errors.append(
                        f"invalid-dep-structure: {tid}[{idx}] not a dict"
                    )
                    continue

                for field in COMMON_REQUIRED_FIELDS:
                    if field not in dep:
                        self.errors.append(
                            f"dep-missing-common-field: {tid}[{idx}] -> "
                            f"{dep.get('task_id', '?')} missing {field}"
                        )

                rtv = dep.get("required_task_version")
                if "required_task_version" in dep:
                    if not isinstance(rtv, int) or rtv < 1:
                        self.errors.append(
                            f"dep-invalid-common-pin: {tid}[{idx}] -> "
                            f"{dep.get('task_id', '?')} required_task_version={rtv!r}"
                        )

                rcv = dep.get("required_change_version")
                if "required_change_version" in dep:
                    if not _is_semver(rcv):
                        self.errors.append(
                            f"dep-invalid-common-pin: {tid}[{idx}] -> "
                            f"{dep.get('task_id', '?')} required_change_version={rcv!r}"
                        )

                dep_type = dep.get("type")
                if dep_type not in ALLOWED_DEP_TYPES:
                    self.errors.append(
                        f"invalid-dep-type: {tid}[{idx}] type={dep_type!r}"
                    )
                    continue

                if dep_type in ("hard", "contract"):
                    if "required_result" not in dep:
                        self.errors.append(
                            f"dep-missing-result: {tid}[{idx}] -> "
                            f"{dep.get('task_id', '?')} missing required_result"
                        )
                    elif dep["required_result"] != "PASS":
                        self.errors.append(
                            f"dep-result-not-pass: {tid}[{idx}] -> "
                            f"{dep.get('task_id', '?')} required_result={dep['required_result']!r}"
                        )

                if dep_type == "contract":
                    for field in ("contract_name", "required_contract_version"):
                        if field not in dep:
                            self.errors.append(
                                f"contract-dep-missing-field: {tid}[{idx}] -> "
                                f"{dep.get('task_id', '?')} missing {field}"
                            )

    def check_dependency_existence(self):
        """核对 dependency 的目标 Task 与指定版本存在，拒绝悬空引用。"""
        for tid, task in self.tasks.items():
            for dep in task["depends_on"]:
                if not isinstance(dep, dict):
                    continue
                dep_id = dep.get("task_id")
                if dep_id is None:
                    self.errors.append(
                        f"dep-missing-task-id: {tid} has dep without task_id"
                    )
                    continue
                if dep_id not in self.tasks:
                    self.errors.append(f"missing-dependency: {tid} -> {dep_id}")
                    continue
                target = self.tasks[dep_id]
                req_tv = dep.get("required_task_version")
                if req_tv is not None and req_tv != target["task_version"]:
                    self.errors.append(
                        f"dep-version-mismatch: {tid} -> {dep_id} "
                        f"requires task_version={req_tv} actual={target['task_version']}"
                    )
                req_cv = dep.get("required_change_version")
                if req_cv is not None and str(req_cv) != str(target["change_version"]):
                    self.errors.append(
                        f"dep-change-version-mismatch: {tid} -> {dep_id} "
                        f"requires change_version={req_cv} actual={target['change_version']}"
                    )

    def check_contract_producers(self):
        """每条 contract 消费边必须精确对应一个 producer 声明及版本。"""
        producers = {}
        for tid, task in self.tasks.items():
            for contract in task["produced_contracts"]:
                name = contract.get("name")
                version = contract.get("version")
                if not name or version is None:
                    self.errors.append(
                        f"invalid-produced-contract: {tid} contract missing name/version"
                    )
                    continue
                if name in producers:
                    self.errors.append(
                        f"duplicate-contract-producer: {name} "
                        f"produced by {producers[name][0]} and {tid}"
                    )
                producers[name] = (tid, str(version))

        for tid, task in self.tasks.items():
            for dep in task["depends_on"]:
                if not isinstance(dep, dict):
                    continue
                if dep.get("type") != "contract":
                    continue
                cname = dep.get("contract_name")
                req_version = dep.get("required_contract_version")
                dep_task_id = dep.get("task_id")
                if cname is None:
                    continue
                if cname not in producers:
                    self.errors.append(
                        f"no-contract-producer: {tid} requires contract {cname!r} "
                        f"but no producer declares it"
                    )
                else:
                    producer_tid, pver = producers[cname]
                    if req_version is not None and str(req_version) != pver:
                        self.errors.append(
                            f"contract-version-mismatch: {tid} requires "
                            f"{cname} version {req_version} but {producer_tid} produces {pver}"
                        )
                    if dep_task_id is not None and dep_task_id != producer_tid:
                        self.errors.append(
                            f"contract-producer-identity-mismatch: {tid} contract edge "
                            f"task_id={dep_task_id} but {cname} is produced by {producer_tid}"
                        )

    def check_no_cycles(self):
        """仅在当前 Task 图中检测环；静态无环不证明运行已完成。"""
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {tid: WHITE for tid in self.tasks}

        def dfs(node):
            color[node] = GRAY
            for dep in self.tasks[node]["depends_on"]:
                if not isinstance(dep, dict):
                    continue
                dep_id = dep.get("task_id")
                if dep_id not in self.tasks:
                    continue
                if color[dep_id] == GRAY:
                    self.errors.append(f"cycle-detected: {node} -> {dep_id}")
                elif color[dep_id] == WHITE:
                    dfs(dep_id)
            color[node] = BLACK

        for tid in self.tasks:
            if color[tid] == WHITE:
                dfs(tid)

    def check_no_later_phase_edges(self):
        """防止早期阶段反向依赖尚未授权的后期阶段结果。"""
        for tid, task in self.tasks.items():
            if not isinstance(task.get("phase"), str) or not PHASE_RE.match(
                task["phase"]
            ):
                continue
            task_phase = _phase_number(task)
            for dep in task["depends_on"]:
                if not isinstance(dep, dict):
                    continue
                dep_id = dep.get("task_id")
                if dep_id not in self.tasks:
                    continue
                dep_task = self.tasks[dep_id]
                if not isinstance(dep_task.get("phase"), str) or not PHASE_RE.match(
                    dep_task["phase"]
                ):
                    continue
                dep_phase = _phase_number(dep_task)
                if dep_phase > task_phase:
                    self.errors.append(
                        f"later-phase-edge: {tid}({task['phase']}) -> "
                        f"{dep_id}({dep_task['phase']})"
                    )

    def check_phase_entry(self):
        """核对显式阶段前置要求；历史阶段编号或 Gate 名称不自动授权执行。"""
        gate_by_phase = {}
        for gate in self.gates:
            gid = gate.get("id", "")
            gphase = gate.get("phase", "")
            if gid and gphase:
                gate_by_phase[gphase] = gate

        for phase, expected_ids in self.phase_entry_tasks.items():
            gate = gate_by_phase.get(phase)
            if gate is None:
                continue
            gate_entry_tasks = gate.get("entry_tasks") or []
            if list(gate_entry_tasks) != list(expected_ids):
                self.errors.append(
                    f"gate-entry-tasks-mismatch: gate {gate.get('id')} phase {phase} "
                    f"entry_tasks={list(gate_entry_tasks)} expected {list(expected_ids)}"
                )

        for phase, ids in self.phase_entry_tasks.items():
            gate = gate_by_phase.get(phase)
            if gate is None:
                if phase not in gate_by_phase:
                    self.errors.append(f"no-gate-for-phase: {phase}")
                continue
            entry_requires = gate.get("entry_requires")
            if not entry_requires or not isinstance(entry_requires, dict):
                self.errors.append(
                    f"gate-missing-entry-requires: {gate.get('id')} for {phase}"
                )
                continue

            for tid in ids:
                if tid not in self.tasks:
                    self.errors.append(
                        f"phase-entry-task-missing: {tid} listed in {phase} entry but not in catalog"
                    )
                    continue
                task = self.tasks[tid]
                pep = task.get("phase_entry_prerequisite")
                if pep is None or not isinstance(pep, dict):
                    self.errors.append(
                        f"phase-entry-missing-prerequisite: {tid} in {phase} "
                        f"entry_tasks but has no phase_entry_prerequisite"
                    )
                    continue

                if "approval_evidence_type" in pep:
                    self.errors.append(
                        f"phase-entry-redeclares-approval-evidence: {tid} "
                        f"must not redeclare approval_evidence_type in phase_entry_prerequisite"
                    )

                prev_gate_id = entry_requires.get("previous_gate_id")
                prev_exit = entry_requires.get("previous_gate_exit_task_id")
                if pep.get("previous_gate_id") != prev_gate_id:
                    self.errors.append(
                        f"phase-entry-gate-mismatch: {tid} previous_gate_id="
                        f"{pep.get('previous_gate_id')!r} expected {prev_gate_id!r}"
                    )
                if pep.get("previous_gate_exit_task_id") != prev_exit:
                    self.errors.append(
                        f"phase-entry-exit-mismatch: {tid} exit_task="
                        f"{pep.get('previous_gate_exit_task_id')!r} expected {prev_exit!r}"
                    )
                req_tv = pep.get("required_exit_task_version")
                if (
                    prev_exit in self.tasks
                    and req_tv != self.tasks[prev_exit]["task_version"]
                ):
                    self.errors.append(
                        f"phase-entry-version-mismatch: {tid} exit_task_version="
                        f"{req_tv} expected {self.tasks[prev_exit]['task_version']}"
                    )
                req_cv = pep.get("required_exit_change_version")
                if prev_exit in self.tasks and str(req_cv) != str(
                    self.tasks[prev_exit]["change_version"]
                ):
                    self.errors.append(
                        f"phase-entry-change-version-mismatch: {tid} "
                        f"exit_change_version={req_cv} expected {self.tasks[prev_exit]['change_version']}"
                    )
                if pep.get("required_gate_result") != "PASS":
                    self.errors.append(
                        f"phase-entry-result-mismatch: {tid} required_gate_result="
                        f"{pep.get('required_gate_result')!r} expected 'PASS'"
                    )
                if pep.get("required_user_approval") != "APPROVED":
                    self.errors.append(
                        f"phase-entry-approval-mismatch: {tid} required_user_approval="
                        f"{pep.get('required_user_approval')!r} expected 'APPROVED'"
                    )

                has_hard_dep_on_exit = False
                for d in task["depends_on"]:
                    if not isinstance(d, dict):
                        continue
                    if d.get("task_id") == prev_exit and d.get("type") == "hard":
                        if d.get("required_result") == "PASS":
                            has_hard_dep_on_exit = True
                        else:
                            self.errors.append(
                                f"phase-entry-hard-dep-result-not-pass: {tid} hard dep on "
                                f"{prev_exit} has required_result={d.get('required_result')!r}"
                            )
                if not has_hard_dep_on_exit:
                    if not any(
                        isinstance(d, dict)
                        and d.get("task_id") == prev_exit
                        and d.get("type") == "hard"
                        for d in task["depends_on"]
                    ):
                        self.errors.append(
                            f"phase-entry-missing-hard-dep: {tid} lacks hard dependency "
                            f"on previous gate exit task {prev_exit}"
                        )

        rev_blocking = {tid: [] for tid in self.tasks}
        for tid, task in self.tasks.items():
            for dep in task["depends_on"]:
                if not isinstance(dep, dict):
                    continue
                dep_id = dep.get("task_id")
                dep_type = dep.get("type")
                if dep_id in rev_blocking and dep_type in ("hard", "contract"):
                    rev_blocking[dep_id].append(tid)

        for phase, phase_ids in self.phase_entry_tasks.items():
            phase_task_ids = [
                tid
                for tid, t in self.tasks.items()
                if isinstance(t.get("phase"), str) and t["phase"] == phase
            ]
            entries = set(phase_ids) & set(phase_task_ids)
            reachable = set()
            queue = deque(entries)
            while queue:
                node = queue.popleft()
                if node in reachable:
                    continue
                reachable.add(node)
                for child in rev_blocking.get(node, []):
                    if self.tasks[child]["phase"] == phase and child not in reachable:
                        queue.append(child)
            for tid in phase_task_ids:
                if tid not in entries and tid not in reachable:
                    self.errors.append(
                        f"phase-entry-ancestry-gap: {tid} in {phase} has no "
                        f"transitive path from any entry task via hard/contract edges"
                    )

        for gate in self.gates:
            exit_task = gate.get("exit_task", "")
            if exit_task not in self.tasks:
                continue
            p0_entries = [
                tid
                for tid in gate.get("entry_tasks", [])
                if tid in self.tasks and self.tasks[tid].get("priority") == "P0"
            ]
            reachable_from_exit = set()
            queue = deque([exit_task])
            while queue:
                node = queue.popleft()
                if node in reachable_from_exit:
                    continue
                reachable_from_exit.add(node)
                if node in self.tasks:
                    for dep in self.tasks[node]["depends_on"]:
                        if not isinstance(dep, dict):
                            continue
                        dep_id = dep.get("task_id")
                        dep_type = dep.get("type")
                        if dep_id in self.tasks and dep_type in ("hard", "contract"):
                            if dep_id not in reachable_from_exit:
                                queue.append(dep_id)
            for entry_tid in p0_entries:
                if entry_tid not in reachable_from_exit:
                    self.errors.append(
                        f"gate-exit-p0-ancestry-gap: {gate.get('id')} exit {exit_task} "
                        f"has no transitive blocking path to P0 entry {entry_tid}"
                    )

    def resolve_owner(self, file_path):
        """按最具体 scope 解析 owner；同等具体度冲突返回 AMBIGUOUS，不任意挑选。"""
        candidates = []
        for scope in self.path_scopes:
            for pattern in scope.get("proposed_paths") or []:
                if _path_matches(pattern, file_path):
                    candidates.append((scope["scope"], scope.get("owner", ""), pattern))
        if not candidates:
            return None
        max_spec = max(_path_specificity(c[2]) for c in candidates)
        top = [c for c in candidates if _path_specificity(c[2]) == max_spec]
        owners = {c[1] for c in top}
        if len(owners) > 1:
            return "AMBIGUOUS"
        return top[0][1]

    def check_owners(self):
        """核对 scope 声明、owner 存在性与重叠关系，不修改路径 claim。"""
        resolution = (self.ws.get("path_ownership") or {}).get("resolution", "")
        if not isinstance(resolution, str) or "most-specific" not in resolution.lower():
            self.errors.append(
                f"invalid-path-ownership-resolution: {resolution!r} "
                f"must use most-specific-wins resolution"
            )

        for scope in self.path_scopes:
            sid = scope.get("scope", "")
            owner = scope.get("owner", "")
            paths = scope.get("proposed_paths") or []
            if not sid:
                self.errors.append("empty-scope-name: path_ownership scope has no name")
            if not owner:
                self.errors.append(f"empty-owner: scope {sid!r} has no owner")
            elif owner not in self.workstreams:
                self.errors.append(
                    f"owner-not-found: scope {sid!r} owner {owner!r} not in workstreams"
                )
            if not paths:
                self.errors.append(
                    f"empty-proposed-paths: scope {sid!r} has no proposed_paths"
                )

        seen_paths = {}
        for scope in self.path_scopes:
            sid = scope.get("scope", "")
            for path in scope.get("proposed_paths") or []:
                if not path:
                    self.errors.append(f"empty-path-in-scope: {sid!r}")
                    continue
                if _path_pattern(path) is None:
                    self.errors.append(
                        f"invalid-path-pattern: scope {sid!r} path {path!r} must use "
                        "whole-segment '*' and terminal '**' grammar"
                    )
                    continue
                if path in seen_paths:
                    self.errors.append(
                        f"ambiguous-path-ownership: {path} claimed by "
                        f"{seen_paths[path]} and {sid}"
                    )
                seen_paths[path] = sid

        claims = []
        for scope in self.path_scopes:
            for path in scope.get("proposed_paths") or []:
                claims.append((scope.get("scope", ""), scope.get("owner", ""), path))
        for index, (left_scope, left_owner, left_path) in enumerate(claims):
            for right_scope, right_owner, right_path in claims[index + 1 :]:
                if left_owner == right_owner:
                    continue
                if _path_specificity(left_path) != _path_specificity(right_path):
                    continue
                if _path_patterns_overlap(left_path, right_path):
                    self.errors.append(
                        "ambiguous-path-ownership: overlapping same-specificity patterns "
                        f"{left_path!r} ({left_scope}) and {right_path!r} "
                        f"({right_scope}) have different owners"
                    )

        seen_scopes = {}
        for scope in self.path_scopes:
            sid = scope.get("scope", "")
            if sid in seen_scopes:
                self.errors.append(f"duplicate-scope: {sid!r}")
            seen_scopes[sid] = True

    def check_cross_source_handoff(self):
        """从 policy 真源核对模板、Catalog 与 runtime 的共享投影，防止多套 handoff 合同漂移。"""
        sources = {}
        if self.ws:
            ho = self.ws.get("orchestration_policy", {}).get("handoff_contract", {})
            if ho:
                sources["workstreams"] = ho
        if self.template:
            t = self.template.get("handoff_contract", {})
            if t:
                sources["template"] = t
        if self.policy:
            p = self.policy.get("subagent_protocol", {})
            if p:
                sources["policy"] = p
        if self.runtime:
            r = self.runtime.get("subagent_protocol", {})
            if r:
                sources["runtime"] = r

        if len(sources) < 4:
            missing = [
                s
                for s in ("workstreams", "template", "policy", "runtime")
                if s not in sources
            ]
            self.errors.append(
                f"cross-source-incomplete: missing handoff data from {missing}"
            )
            return

        # 只从 policy 加载规范真源，逐一核对其他投影。
        canonical = _load_canonical_from_policy(self.policy)
        if canonical is None:
            self.errors.append(
                "cross-source-handoff: cannot load canonical reference from policy"
            )
            return
        canonical_caller = canonical["caller_fields"]
        canonical_result = canonical["result_fields"]
        canonical_identity = canonical["runner_identity"]
        canonical_adapter_flags = canonical["adapter_flags"]
        canonical_adapter_metadata = canonical["adapter_metadata"]
        canonical_schema = canonical["caller_field_schema"]
        canonical_enforced = canonical["adapter_enforced_fields"]

        names = list(sources.keys())

        def _extract_field_names(value):
            if isinstance(value, list):
                return list(value)
            if isinstance(value, dict):
                return list(value.keys())
            return []

        for n in names:
            caller_fields = _extract_field_names(
                sources[n].get("caller_required_input")
            )
            if len(caller_fields) != len(canonical_caller):
                self.errors.append(
                    f"canonical-caller-count: {n} has {len(caller_fields)} caller fields, "
                    f"expected {len(canonical_caller)}"
                )
            if caller_fields != canonical_caller:
                self.errors.append(
                    f"canonical-caller-fields: {n} caller_required_input does not match "
                    f"canonical field names and order"
                )

        for n in names:
            result_fields = _extract_field_names(
                sources[n].get("result_required_output")
            )
            if len(result_fields) != len(canonical_result):
                self.errors.append(
                    f"canonical-result-count: {n} has {len(result_fields)} result fields, "
                    f"expected {len(canonical_result)}"
                )
            if result_fields != canonical_result:
                self.errors.append(
                    f"canonical-result-fields: {n} result_required_output does not match "
                    f"canonical field names and order"
                )

        for n in names:
            identity = sources[n].get("runner_bound_identity", {})
            if identity != canonical_identity:
                self.errors.append(
                    f"canonical-runner-identity: {n} runner_bound_identity does not match "
                    f"canonical mapping"
                )

        for n in names:
            adapter = sources[n].get("current_runner_adapter", {})
            if not isinstance(adapter, dict):
                self.errors.append(
                    f"canonical-adapter-structure: {n} current_runner_adapter must be a map"
                )
                continue
            ecf = adapter.get("enforced_caller_fields", [])
            if list(ecf) != canonical_enforced:
                self.errors.append(
                    f"canonical-adapter-enforced-fields: {n} enforced_caller_fields does not "
                    f"match canonical caller fields"
                )
            flags = {
                "target_schema_enforced": adapter.get("target_schema_enforced"),
                "runtime_identity_enforced": adapter.get("runtime_identity_enforced"),
                "result_fields_required_in_prompt": adapter.get(
                    "result_fields_required_in_prompt"
                ),
                "agent_profile_cli_binding": adapter.get("agent_profile_cli_binding"),
                "harness_manifest_preflight": adapter.get("harness_manifest_preflight"),
                "catalog_package_preflight": adapter.get("catalog_package_preflight"),
                "catalog_estimate_rule": adapter.get("catalog_estimate_rule"),
                "structured_result_required": adapter.get("structured_result_required"),
            }
            if flags != canonical_adapter_flags:
                self.errors.append(
                    f"canonical-adapter-flags: {n} adapter enforcement flags do not match "
                    f"canonical values"
                )
            metadata = {
                "implementation_status": adapter.get("implementation_status"),
                "conformance_test": adapter.get("conformance_test"),
                "owner_task": adapter.get("owner_task"),
                "owner_task_version": adapter.get("owner_task_version"),
                "owner_change_version": adapter.get("owner_change_version"),
            }
            if metadata != canonical_adapter_metadata:
                self.errors.append(
                    f"canonical-adapter-metadata: {n} adapter owner metadata does not match "
                    f"canonical values"
                )

        for n in names:
            schema = sources[n].get("caller_field_schema")
            if schema is not None and (
                not isinstance(schema, dict) or schema != canonical_schema
            ):
                self.errors.append(
                    f"canonical-caller-field-schema: {n} caller_field_schema does not match "
                    f"canonical key-value mapping"
                )

        if not any(
            isinstance(sources[n].get("caller_field_schema"), dict) for n in names
        ):
            self.errors.append(
                "canonical-caller-field-schema: all sources omit caller_field_schema"
            )

        caller_lists = {
            n: _extract_field_names(sources[n].get("caller_required_input"))
            for n in names
        }
        for i in range(1, len(names)):
            if caller_lists[names[i]] != caller_lists[names[0]]:
                self.errors.append(
                    f"cross-source-caller-mismatch: {names[0]} vs {names[i]} "
                    f"caller_required_input differs"
                )
                break

        result_lists = {
            n: _extract_field_names(sources[n].get("result_required_output"))
            for n in names
        }
        for i in range(1, len(names)):
            if result_lists[names[i]] != result_lists[names[0]]:
                self.errors.append(
                    f"cross-source-result-mismatch: {names[0]} vs {names[i]} "
                    f"result_required_output differs"
                )
                break

        identity_maps = {n: sources[n].get("runner_bound_identity", {}) for n in names}
        for i in range(1, len(names)):
            if identity_maps[names[i]] != identity_maps[names[0]]:
                self.errors.append(
                    f"cross-source-identity-mismatch: {names[0]} vs {names[i]} "
                    f"runner_bound_identity metadata differs"
                )
                break

        adapter_maps = {}
        for n in names:
            adapter = sources[n].get("current_runner_adapter", {})
            if isinstance(adapter, dict):
                adapter_maps[n] = {
                    "target_schema_enforced": adapter.get("target_schema_enforced"),
                    "runtime_identity_enforced": adapter.get(
                        "runtime_identity_enforced"
                    ),
                    "result_fields_required_in_prompt": adapter.get(
                        "result_fields_required_in_prompt"
                    ),
                    "agent_profile_cli_binding": adapter.get(
                        "agent_profile_cli_binding"
                    ),
                    "harness_manifest_preflight": adapter.get(
                        "harness_manifest_preflight"
                    ),
                    "catalog_package_preflight": adapter.get(
                        "catalog_package_preflight"
                    ),
                    "catalog_estimate_rule": adapter.get("catalog_estimate_rule"),
                    "structured_result_required": adapter.get(
                        "structured_result_required"
                    ),
                    "enforced_caller_fields": adapter.get("enforced_caller_fields", []),
                }
            else:
                adapter_maps[n] = {}
        for i in range(1, len(names)):
            if adapter_maps[names[i]] != adapter_maps[names[0]]:
                self.errors.append(
                    f"cross-source-adapter-mismatch: {names[0]} vs {names[i]} "
                    f"current_runner_adapter metadata differs"
                )
                break

        schema_maps = {}
        for n in names:
            s = sources[n].get("caller_field_schema")
            if s is not None and isinstance(s, dict):
                schema_maps[n] = s
        schema_names = list(schema_maps.keys())
        if len(schema_names) >= 2:
            for i in range(1, len(schema_names)):
                if schema_maps[schema_names[i]] != schema_maps[schema_names[0]]:
                    self.errors.append(
                        f"cross-source-schema-mismatch: {schema_names[0]} vs {schema_names[i]} "
                        f"caller_field_schema key-value mapping differs"
                    )
                    break

    def check_repo_path_ownership(self):
        """将非运行产物文件映射到 owner；发现无 owner 或歧义时保留文件路径。"""
        if not self.root.exists():
            return
        governance_files = []
        for item in self.root.rglob("*"):
            if not item.is_file():
                continue
            rel = str(item.relative_to(self.root))
            if _is_excluded(rel):
                continue
            governance_files.append(rel)

        for fpath in governance_files:
            owner = self.resolve_owner(fpath)
            if owner is None:
                self.errors.append(f"repo-unowned-file: {fpath} has no owning scope")
            elif owner == "AMBIGUOUS":
                self.errors.append(
                    f"repo-ambiguous-owner: {fpath} resolves to multiple owners at same specificity"
                )

    def run_all(self):
        """先处理加载/结构错误，再运行确定性的静态检查；planning-only 也不能跳过基础约束。"""
        if self._load_errors:
            return {
                "status": "FAIL",
                "task_count": 0,
                "errors": list(self._load_errors),
                "checks_run": [],
            }
        if self._structure_errors:
            return {
                "status": "FAIL",
                "task_count": 0,
                "errors": list(self._structure_errors),
                "checks_run": [],
            }
        program = self.ws.get("program") or {}
        if not isinstance(program, dict):
            return {
                "status": "FAIL",
                "task_count": len(self.tasks),
                "errors": ["invalid-program-structure"],
                "checks_run": [],
            }
        planning_only = program.get("catalog_mode") == "planning-only"
        if planning_only and (
            self.tasks
            or self.gates
            or self.phase_entry_tasks
            or any(
                program.get(key) is not None
                for key in (
                    "current_phase",
                    "current_gate",
                    "phase_2_to_6_dispatch_requires_g1_user_approval",
                )
            )
        ):
            return {
                "status": "FAIL",
                "task_count": len(self.tasks),
                "errors": ["planning-only-catalog-has-execution-state"],
                "checks_run": [],
            }
        if not self.tasks and not planning_only:
            return {
                "status": "FAIL",
                "task_count": 0,
                "errors": ["no-tasks-found-in-catalog"],
                "checks_run": [],
            }

        checks = [
            ("all_entity_ids", self.check_all_entity_ids),
            ("valid_versions", self.check_versions),
            ("required_check_ids", self.check_required_check_ids),
            ("dependency_types", self.check_dependency_types),
            ("dependency_existence", self.check_dependency_existence),
            ("contract_producers", self.check_contract_producers),
            ("no_cycles", self.check_no_cycles),
            ("no_later_phase_edges", self.check_no_later_phase_edges),
            ("phase_entry", self.check_phase_entry),
            ("owners", self.check_owners),
            ("cross_source_handoff", self.check_cross_source_handoff),
            ("repo_path_ownership", self.check_repo_path_ownership),
        ]
        checks_run = []
        for name, fn in checks:
            self._check(fn)
            checks_run.append(name)

        if self._check_failures:
            status = "FAIL"
        else:
            status = "BLOCKED" if self.errors else "PASS"
        return {
            "status": status,
            "task_count": len(self.tasks),
            "errors": list(self.errors),
            "checks_run": checks_run,
        }
