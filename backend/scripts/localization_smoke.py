import asyncio

from app.engines.localization_engine import LocalizationEngine
from app.models.task import TaskRecord


async def main() -> None:
    eng = LocalizationEngine()
    task_id = "smoke_local"
    record = TaskRecord(
        task_id=task_id,
        service="localization",
        mode="baseline",
        input_video_url="",  # TODO: set a reachable public URL before running
        input_key=None,
    )
    inputs = {
        "inputs": {
            "target_lang": "my",
            "voice_id": "mm_female_1",
            "subtitle_mode": "sidecar",
            "preserve_bgm": True,
            "ducking": True,
        }
    }

    def on_log(msg: str) -> None:
        print(msg, flush=True)

    def on_stage(stage: str, prog: int) -> None:
        print(f"[stage] {stage} {prog}", flush=True)

    await eng.run(task_id=task_id, record=record, inputs=inputs, on_log=on_log, on_stage=on_stage)


if __name__ == "__main__":
    asyncio.run(main())
