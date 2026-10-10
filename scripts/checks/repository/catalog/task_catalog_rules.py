"""Planning Catalog 的静态一致性检查。

依次核对 ID、owner、dependency 类型与 producer、DAG、阶段前置条件及唯一策略合同。
这是 Repository 维护能力，不负责启动产品测试，也不授予 Task 执行权限。"""

import hashlib
import re
import subprocess
from collections import deque
from pathlib import Path

import yaml

from scripts.agents.work_packages.work_package_contract_checks import (
    AgentContractError,
    dispatch_paths_intersect,
    match_dispatch_path_v1,
    parse_dispatch_path_v1,
)

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

ALLOWED_DEP_TYPES = {"hard", "soft", "contract"}

COMMON_REQUIRED_FIELDS = {
    "task_id",
    "type",
    "required_task_version",
    "required_change_version",
}


PRIVATE_ROOTS = frozenset(
    {".git", ".idea", ".vscode", "tmp", ".local", "data", "secrets", ".agents"}
)
SHARED_ENTRY_EXEMPTIONS = frozenset(
    {
        "AGENTS.md",
        "README.md",
        "requirements.txt",
        "requirements-dev.txt",
        "extension/package-lock.json",
    }
)


def repository_source_inventory(root):
    """枚举共享 Git 源候选与有效共享 ignore 哈希，不读取源码或个人排除配置。"""
    root = Path(root).resolve()

    def git(*args):
        return subprocess.run(
            ["git", "-c", "core.excludesFile=/dev/null", *args],
            cwd=root,
            capture_output=True,
            check=True,
        ).stdout

    actual = Path(git("rev-parse", "--show-toplevel").decode().strip()).resolve()
    if actual != root:
        raise ValueError("ownership inventory requires the exact repository root")
    names = sorted(
        set(
            filter(
                None,
                git(
                    "ls-files",
                    "--cached",
                    "--others",
                    "--exclude-per-directory=.gitignore",
                    "-z",
                )
                .decode()
                .split("\0"),
            )
        )
    )
    paths = []
    ignore_paths = {".gitignore"}
    for name in names:
        kind, parts = parse_dispatch_path_v1(name)
        if kind != "exact":
            raise ValueError("ownership candidate must be an exact path")
        if parts[0] in PRIVATE_ROOTS:
            continue
        if parts[-1] == ".gitignore":
            ignore_paths.add(name)
        # 共享规则即使忽略自身，仍是公开候选的有效祖先输入；只沿候选祖先定位，不遍历私有树。
        for depth in range(1, len(parts)):
            candidate = "/".join((*parts[:depth], ".gitignore"))
            if (root / candidate).exists():
                ignore_paths.add(candidate)
        if name not in SHARED_ENTRY_EXEMPTIONS:
            paths.append(name)
    ignores = {}
    for name in sorted(ignore_paths):
        target = root / name
        if target.is_symlink() or any(
            parent.is_symlink()
            for parent in target.parents
            if parent != root and parent.is_relative_to(root)
        ):
            raise ValueError("shared ignore cannot be a symlink")
        ignores[name] = (
            hashlib.sha256(target.read_bytes()).hexdigest()
            if target.exists()
            else "missing"
        )
    return {"candidate_paths": paths, "shared_ignore_sha256": ignores}


def _phase_number(task):
    return int(task["phase"][1:])


def _is_semver(value):
    return (
        isinstance(value, (str, int, float)) and SEMVER_RE.match(str(value)) is not None
    )


def _path_specificity(pattern):
    try:
        kind, prefix = parse_dispatch_path_v1(pattern)
    except AgentContractError:
        return (-1, -1)
    return (len(prefix), {"subtree": 0, "child": 1, "exact": 2}[kind])


class PlanningValidator:
    """按共享 contract 核对 planning/workstreams.yaml，累积可定位的错误。"""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self._load_errors = []
        self._structure_errors = []
        self._check_failures = []
        self.ws = None
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
        module_checks_data=None,
    ):
        """由已加载的规划数据构造校验器，供隔离测试复用。"""
        obj = cls(Path("."))
        obj.ws = ws_data
        obj.module_checks = module_checks_data
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
        """读取 Catalog 与机器 Check 真源，缺项不能当作空规划。"""
        self.ws = self._load_yaml("planning/workstreams.yaml")
        # Catalog 只消费选择列表中的身份与 scope；执行定义由 Verify 加载器校验。
        self.module_checks = self._load_yaml("config/engineering/module-checks.yaml")
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

        if "phase_entry_validation" in self.ws:
            self._structure_errors.append(
                "invalid-structure: phase_entry_validation is retired; use Gate.entry_tasks"
            )
        self.gates = list(gates_raw or [])
        self.path_scopes = list(scopes_raw or [])

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

                    cap_phase = cap.get(
                        "phase", epic.get("phase", ws.get("phase", "P1"))
                    )
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
        for gate in self.gates:
            phase = gate.get("phase")
            if phase in self.phase_entry_tasks:
                self._structure_errors.append(f"duplicate-gate-phase: {phase}")
            else:
                self.phase_entry_tasks[phase] = list(gate.get("entry_tasks") or [])

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

        # Shared handoff rules live only in agent-policy; task/runtime/catalog
        # projections are intentionally not required or compared here.

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
                "required-check-registry: config/engineering/module-checks.yaml checks missing"
            )
            return
        declared = {
            item.get("check_id")
            for item in checks
            if isinstance(item, dict)
            and "repository-baseline" in item.get("scenarios", {})
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

        for phase, ids in self.phase_entry_tasks.items():
            gate = gate_by_phase.get(phase)
            if gate is None:
                if phase not in gate_by_phase:
                    self.errors.append(f"no-gate-for-phase: {phase}")
                continue
            for tid in ids:
                if tid not in self.tasks:
                    self.errors.append(
                        f"phase-entry-task-missing: {tid} listed in {phase} entry but not in catalog"
                    )
                elif self.tasks[tid].get("phase") != phase:
                    self.errors.append(
                        f"phase-entry-task-phase-mismatch: {tid} must belong to {phase}"
                    )
            entry_requires = gate.get("entry_requires")
            if entry_requires is None and phase == "P1":
                continue
            if not entry_requires or not isinstance(entry_requires, dict):
                self.errors.append(
                    f"gate-missing-entry-requires: {gate.get('id')} for {phase}"
                )
                continue

            for tid in ids:
                if tid not in self.tasks:
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
                if match_dispatch_path_v1(file_path, pattern):
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
                try:
                    parse_dispatch_path_v1(path)
                except AgentContractError:
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
                if _path_specificity(left_path) == (-1, -1):
                    continue
                if dispatch_paths_intersect(left_path, right_path):
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

    def check_repo_path_ownership(self):
        """将非运行产物文件映射到 owner；发现无 owner 或歧义时保留文件路径。"""
        try:
            frozen = repository_source_inventory(self.root)
            for fpath in frozen["candidate_paths"]:
                owner = self.resolve_owner(fpath)
                if owner is None:
                    self.errors.append(
                        f"repo-unowned-file: {fpath} has no owning scope"
                    )
                elif owner == "AMBIGUOUS":
                    self.errors.append(
                        f"repo-ambiguous-owner: {fpath} resolves to multiple owners at same specificity"
                    )
            if repository_source_inventory(self.root) != frozen:
                self.errors.append(
                    "repo-ownership-input-drift: shared source inventory changed"
                )
        except (OSError, ValueError, subprocess.CalledProcessError) as exc:
            self.errors.append(f"repo-ownership-inventory-unavailable: {exc}")

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
