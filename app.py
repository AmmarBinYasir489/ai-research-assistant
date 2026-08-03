import threading
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from main import run_research
from tools.llm import get_provider_status

ROOT = Path(__file__).parent
WEB_DIR = ROOT / "web"

app = FastAPI(title="Research Assistant Agent")

JOBS: dict[str, dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()


class ResearchRequest(BaseModel):
    question: str


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/config")
def config() -> dict[str, Any]:
    return {"llm_providers": get_provider_status()}


@app.post("/api/research")
def start_research(request: ResearchRequest) -> dict[str, str]:
    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question must not be empty.")

    job_id = uuid.uuid4().hex
    job = {
        "id": job_id,
        "question": question,
        "status": "running",
        "progress": [],
        "result": None,
        "error": None,
    }
    with JOBS_LOCK:
        if len(JOBS) > 50:
            stale = [key for key, item in JOBS.items() if item["status"] != "running"]
            for key in stale:
                JOBS.pop(key, None)
        JOBS[job_id] = job

    def on_progress(event: dict[str, str]) -> None:
        with JOBS_LOCK:
            job["progress"].append(event)

    def worker() -> None:
        try:
            result = run_research(question, on_progress=on_progress)
            with JOBS_LOCK:
                job["status"] = "done"
                job["result"] = {
                    "question": result.question,
                    "plan": dict(result.plan),
                    "answer": result.answer,
                    "evidence": dict(result.evaluation),
                    "sources": [dict(source) for source in result.sources],
                    "trace": [dict(item) for item in result.trace],
                }
        except Exception as error:  # noqa: BLE001
            with JOBS_LOCK:
                job["status"] = "error"
                job["error"] = str(error)

    threading.Thread(target=worker, daemon=True).start()
    return {"job_id": job_id}


@app.get("/api/research/{job_id}")
def research_status(job_id: str) -> dict[str, Any]:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")

    return {
        "status": job["status"],
        "progress": list(job["progress"]),
        "result": job["result"],
        "error": job["error"],
    }
