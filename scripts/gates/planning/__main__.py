"""CLI entry point: python3 -m scripts.gates.planning --root <path>"""

import argparse
import sys

from scripts.gates.planning import PlanningValidator


def main():
    parser = argparse.ArgumentParser(description="LexiFlow planning catalog validator")
    parser.add_argument("--root", default=".", help="Project root directory")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    validator = PlanningValidator(args.root)
    validator.load_sources()
    result = validator.run_all()

    print(f"Planning validator: {result['status']}")
    print(f"Tasks validated: {result['task_count']}")
    print(f"Checks executed: {len(result['checks_run'])} ({', '.join(result['checks_run'])})")

    if result["errors"]:
        print(f"\nDiagnostics ({len(result['errors'])}):")
        for error in result["errors"]:
            print(f"  - {error}")

    if args.verbose and not result["errors"]:
        print("\nAll planning checks passed.")

    sys.exit(0 if result["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
