"""Run the whole pipeline in order.

    python run_pipeline.py                # full run (needs GEOAPIFY_API_KEY for new stops)
    python run_pipeline.py --skip-lookup  # never call Geoapify, use cache / manual entries only

Steps can also be run one at a time, e.g.  python src/step3_geocode.py
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STEPS = [
    "src/step1_clean.py",
    "src/step2_pickup_points.py",
    "src/step3_geocode.py",
    "src/step4_master.py",
    "src/step5_distances.py",
    "src/step6_grouping.py",
    "src/step7_final_workbook.py",
]


def main():
    extra = ["--skip-lookup"] if "--skip-lookup" in sys.argv else []
    for step in STEPS:
        print(f"\n===== {step} =====", flush=True)
        args = [sys.executable, str(ROOT / step)]
        if step.endswith("step3_geocode.py"):
            args += extra
        result = subprocess.run(args, cwd=ROOT)
        if result.returncode != 0:
            print(f"\nStopped: {step} exited with code {result.returncode}")
            sys.exit(result.returncode)
    print("\nPipeline finished. Start with output/07_final_bus_routing_workbook.xlsx")


if __name__ == "__main__":
    main()
