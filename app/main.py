"""FastAPI application: project persistence + authoritative evaluation.

Save semantics: every PUT validates the *whole* new project through the
engine before any write; validation failure returns HTTP 400 and the stored
project is untouched (write happens inside one SQLite transaction).
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db, engine
from .schemas import CreateProject, ProjectPayload
from .seed import DEMO_ID, DEMO_PROJECT


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    with db.connect() as conn:
        if db.get_project(conn, DEMO_ID) is None:
            engine.compile_project(DEMO_PROJECT)  # fail fast on a bad demo
            db.replace_project(
                conn, DEMO_ID, DEMO_PROJECT["name"],
                [_cue_to_plain(c) for c in DEMO_PROJECT["cues"]], create=True)
    yield


app = FastAPI(title="灯光渐变时间轴", version="1.0.0", lifespan=lifespan)


def _cue_to_plain(c: dict) -> dict:
    return {"id": c["id"], "fixture": c["fixture"], "channel": c["channel"],
            "target": c["target"], "duration": c["duration"],
            "start": c["start"]}


def _payload_cues(payload: ProjectPayload | CreateProject) -> list[dict]:
    return [c.model_dump() for c in payload.cues]


def _load_or_404(conn, project_id: str) -> dict:
    saved = db.get_project(conn, project_id)
    if saved is None:
        raise HTTPException(404, "工程不存在")
    return saved


def _save(project_id: str, payload: ProjectPayload | CreateProject,
          *, create: bool) -> dict:
    raw = {"name": payload.name, "cues": _payload_cues(payload)}
    try:
        compiled = engine.compile_project(raw)  # full validation, no I/O
    except engine.EngineError as exc:
        raise HTTPException(status_code=400, detail={
            "message": "工程校验失败，未保存", "errors": exc.errors})
    with db.connect() as conn:
        try:
            saved = db.replace_project(conn, project_id, payload.name,
                                       raw["cues"], create=create)
        except KeyError:
            raise HTTPException(404, "工程不存在")
    return {"project": saved, "timeline": compiled}


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.get("/api/projects")
def api_list_projects() -> dict:
    with db.connect() as conn:
        return {"projects": db.list_projects(conn)}


@app.post("/api/projects", status_code=201)
def api_create_project(payload: CreateProject) -> dict:
    project_id = db.new_id()
    return _save(project_id, payload, create=True) | {"id": project_id}


@app.get("/api/projects/{project_id}")
def api_get_project(project_id: str, include_timeline: bool = True) -> dict:
    with db.connect() as conn:
        saved = _load_or_404(conn, project_id)
    result: dict = {"project": saved}
    if include_timeline:
        result["timeline"] = engine.compile_project(saved)
    return result


@app.put("/api/projects/{project_id}")
def api_save_project(project_id: str, payload: ProjectPayload) -> dict:
    return _save(project_id, payload, create=False)


@app.post("/api/projects/{project_id}/validate")
def api_validate(project_id: str, payload: ProjectPayload) -> dict:
    """Compile a candidate payload WITHOUT touching the stored project."""
    with db.connect() as conn:
        _load_or_404(conn, project_id)
    raw = {"name": payload.name, "cues": _payload_cues(payload)}
    try:
        compiled = engine.compile_project(raw)
    except engine.EngineError as exc:
        raise HTTPException(status_code=400, detail={
            "message": "工程校验失败", "errors": exc.errors})
    return {"ok": True, "timeline": compiled}


@app.post("/api/projects/{project_id}/evaluate")
async def api_evaluate(project_id: str, request: Request) -> dict:
    """Authoritative evaluation at one time: {time: number|{n,d}}."""
    body = await request.json()
    with db.connect() as conn:
        saved = _load_or_404(conn, project_id)
    t = engine.parse_time(body.get("time"))
    if t is None:
        raise HTTPException(400, "time 必须是非负整数、有限非负小数或 {n,d}")
    return engine.evaluate(engine.compile_project(saved), t)


@app.exception_handler(RequestValidationError)
async def _ve_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = [{"loc": list(e.get("loc", [])), "field": _field_name(e),
               "message": e.get("msg", "字段不合法"), "code": "invalid_field"}
              for e in exc.errors()]
    return JSONResponse(status_code=400,
                        content={"detail": {"message": "请求字段不合法",
                                            "errors": errors}})


def _field_name(err: dict) -> str:
    loc = [str(p) for p in err.get("loc", []) if p != "body"]
    return ".".join(loc) if loc else None


# Static UI (mounted last so /api routes take precedence)
_STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                           "static")
app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")
