import hashlib
import json
import re
import secrets
import sqlite3
import time
from typing import Literal
from uuid import UUID

from aica_runtime.graph.contract import GraphDocument
from aica_schema.protocol import new_id, require, require_archive, require_dag, utc_now
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

SESSION_SECONDS = 7 * 24 * 3600


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Register(Input):
    username: str = Field(pattern=r"^[a-z][a-z0-9-]{2,31}$")
    password: str = Field(min_length=12, max_length=128)
    name: str = Field(min_length=1, max_length=80)


class Login(Input):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=128)


class NewRepository(Input):
    name: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}$")
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    visibility: Literal["private", "public"] = "private"


class Branch(Input):
    name: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,79}$")
    from_branch: str = Field(default="main", min_length=1, max_length=80)
    from_commit: UUID | None = Field(default=None, strict=False)


class Commit(Input):
    branch: str = Field(default="main", min_length=1, max_length=80)
    expected_head: UUID | None = Field(strict=False)
    message: str = Field(min_length=1, max_length=500)
    document: dict


class ArchiveCommit(Input):
    branch: str = Field(default="main", min_length=1, max_length=80)
    expected_head: UUID | None = Field(strict=False)
    message: str = Field(min_length=1, max_length=500)
    project_id: UUID = Field(strict=False)
    import_id: UUID = Field(strict=False)


class Fork(NewRepository):
    branch: str = Field(default="main", min_length=1, max_length=80)


class SemanticCommit(ArchiveCommit):
    graph_id: str = Field(min_length=1, max_length=200)
    revision_id: str = Field(min_length=1, max_length=200)


class Visibility(Input):
    visibility: Literal["private", "public"]


class UploadToken(Input):
    project_id: UUID = Field(strict=False)


def validate_document(document):
    try:
        raw = json.dumps(document, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (ValueError, TypeError, RecursionError):
        raise HTTPException(422, "Invalid Graph document") from None
    pending = [(document, 0)]
    sensitive = re.compile(r"^(api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|password|secret|credentials|client[_-]?secret)$", re.I)
    while pending:
        item, depth = pending.pop()
        require(depth <= 40, "Document nesting exceeds limit")
        if isinstance(item, dict):
            require(not any(sensitive.fullmatch(key) for key in item), "Remove credential fields before upload")
            pending.extend((value, depth + 1) for value in item.values())
        elif isinstance(item, list):
            pending.extend((value, depth + 1) for value in item)
    if "aica_version" in document:
        require_archive(document)
        return raw, "aica"
    if document.get("format") == "aica-semantic-graph/0.1":
        try:
            graph = GraphDocument.model_validate(document)
        except ValidationError:
            raise HTTPException(422, "Invalid semantic Graph document") from None
        ids = {node.id for node in graph.nodes}
        require(len(ids) == len(graph.nodes) and bool(ids), "Invalid semantic node identities")
        evidence = {item.id for item in graph.evidence}
        for node in graph.nodes:
            require(set(node.evidence_ids) <= evidence, "Unknown semantic evidence")
        for edge in graph.edges:
            require(edge.source in ids and edge.target in ids, "Unknown semantic edge endpoint")
        require_dag(ids, [(edge.source, edge.target) for edge in graph.edges if edge.kind != "semantic"])
        return raw, "aica-semantic-graph/0.1"
    nodes = document.get("modules", document.get("steps"))
    require(isinstance(nodes, list) and 0 < len(nodes) <= 200, "Expected 1-200 modules or steps")
    ids = set()
    for node in nodes:
        require(isinstance(node, dict) and isinstance(node.get("id"), str)
                and 0 < len(node["id"].strip()) <= 180 and node["id"] not in ids, "Invalid or duplicate module ID")
        require(isinstance(node.get("prompt", ""), str) and len(node.get("prompt", "")) <= 20000, "Invalid prompt")
        ids.add(node["id"])
    edges = document.get("edges", [])
    require(isinstance(edges, list) and len(edges) <= 40000, "Invalid edges")
    dependencies = {key: set() for key in ids}
    for edge in edges:
        require(isinstance(edge, dict) and isinstance(edge.get("source"), str)
                and isinstance(edge.get("target"), str) and edge["source"] in ids and edge["target"] in ids,
                "Invalid dependency reference")
        dependencies[edge["target"]].add(edge["source"])
    for node in nodes:
        parents = node.get("depends_on", node.get("dependencies", []))
        require(isinstance(parents, list) and all(isinstance(parent, str) and parent in ids for parent in parents),
                "Invalid dependency reference")
        dependencies[node["id"]].update(parents)
    ready = [key for key, parents in dependencies.items() if not parents]
    visited = set()
    while ready:
        key = ready.pop()
        if key in visited:
            continue
        visited.add(key)
        for child, parents in dependencies.items():
            if key in parents:
                parents.remove(key)
                if not parents:
                    ready.append(child)
    require(len(visited) == len(ids), "Graph dependencies contain a cycle")
    golden = document.get("goldenPath", document.get("golden_path", []))
    require(isinstance(golden, list) and all(isinstance(key, str) and key in ids for key in golden),
            "Invalid Golden Path reference")
    return raw, "ui-module-record"


class Platform:
    def __init__(self, store):
        self.store = store
        self.dummy_salt = secrets.token_bytes(16)
        self.dummy_hash = self.password_hash("not-a-user-password", self.dummy_salt)
        with store.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
                    salt BLOB NOT NULL, password_hash BLOB NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    digest TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), expires REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS upload_tokens (
                    id TEXT PRIMARY KEY, digest TEXT NOT NULL UNIQUE,
                    user_id TEXT NOT NULL REFERENCES users(id),
                    project_id TEXT NOT NULL REFERENCES projects(id), expires REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS auth_limits (
                    key TEXT PRIMARY KEY, attempts INTEGER NOT NULL, reset REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS repositories (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL COLLATE NOCASE,
                    title TEXT NOT NULL, description TEXT NOT NULL, visibility TEXT NOT NULL,
                    created_at TEXT NOT NULL, parent_repo TEXT REFERENCES repositories(id), parent_commit TEXT,
                    UNIQUE(owner, name)
                );
                CREATE TABLE IF NOT EXISTS repo_commits (
                    id TEXT PRIMARY KEY, repo_id TEXT NOT NULL REFERENCES repositories(id),
                    parent TEXT REFERENCES repo_commits(id), author TEXT NOT NULL, message TEXT NOT NULL,
                    created_at TEXT NOT NULL, document TEXT NOT NULL, document_hash TEXT NOT NULL,
                    format TEXT NOT NULL, project_id TEXT, import_id TEXT, source_commit TEXT
                );
                CREATE TABLE IF NOT EXISTS repo_branches (
                    repo_id TEXT NOT NULL REFERENCES repositories(id), name TEXT NOT NULL,
                    head TEXT REFERENCES repo_commits(id), created_at TEXT NOT NULL, PRIMARY KEY(repo_id, name)
                );
                CREATE INDEX IF NOT EXISTS repo_commit_history ON repo_commits(repo_id, created_at, id);
                CREATE TABLE IF NOT EXISTS parsed_project_repositories (
                    project_id TEXT PRIMARY KEY REFERENCES projects(id),
                    repo_id TEXT NOT NULL UNIQUE REFERENCES repositories(id)
                );
                CREATE TABLE IF NOT EXISTS import_publications (
                    import_id TEXT PRIMARY KEY REFERENCES imports(id),
                    repo_id TEXT NOT NULL REFERENCES repositories(id),
                    commit_id TEXT NOT NULL UNIQUE REFERENCES repo_commits(id)
                );
                CREATE TABLE IF NOT EXISTS parse_publications (
                    parse_id TEXT PRIMARY KEY, repo_id TEXT NOT NULL REFERENCES repositories(id),
                    commit_id TEXT NOT NULL REFERENCES repo_commits(id)
                );
            """)

        self.store.import_publisher = self.publish_import

    def publish_import(self, db, subject, project_id, import_id=None):
        project = self.store.owned_project(db, subject, project_id)
        if not subject.startswith("user:") or not project["head"]:
            return None
        # Project head is authoritative: a late retry cannot restore an older import.
        import_id = project["head"]
        prior = db.execute("SELECT repo_id,commit_id FROM import_publications WHERE import_id=?", (import_id,)).fetchone()
        if prior:
            self.access(db, prior["repo_id"], subject, write=True)
            return dict(prior)
        row = db.execute("SELECT archive,archive_hash FROM imports WHERE id=? AND project_id=?", (import_id, project_id)).fetchone()
        require(row is not None, "Current import missing", "storage_corrupt")
        require(hashlib.sha256(row["archive"]).hexdigest() == row["archive_hash"], "Stored archive is corrupt", "storage_corrupt")
        archive = json.loads(row["archive"])
        document = {**archive, "extensions": {key: value for key, value in archive.get("extensions", {}).items()
                                                if key != "aica.embedded_artifacts"}}
        raw, format_name = validate_document(document)
        binding = db.execute("SELECT repo_id FROM parsed_project_repositories WHERE project_id=?", (project_id,)).fetchone()
        if binding:
            repo_id = binding["repo_id"]
            self.access(db, repo_id, subject, write=True)
        else:
            repo_id = self.create(db, subject, NewRepository(name="imported-" + project_id,
                title=(archive["metadata"]["title"] or project["title"])[:200],
                description="MCP创作原始归档，未经AI提炼。", visibility="private"))["id"]
            db.execute("INSERT INTO parsed_project_repositories VALUES (?,?)", (project_id, repo_id))
        parent = self.branch(db, repo_id, "main")["head"]
        identifier = new_id()
        db.execute("INSERT INTO repo_commits VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL)",
                   (identifier, repo_id, parent, subject, "同步 MCP 原始归档", utc_now(), raw,
                    hashlib.sha256(raw.encode()).hexdigest(), format_name, project_id, import_id))
        db.execute("UPDATE repositories SET visibility='private',title=? WHERE id=?",
                   ((archive["metadata"]["title"] or project["title"])[:200], repo_id))
        db.execute("UPDATE repo_branches SET head=? WHERE repo_id=? AND name='main'", (identifier, repo_id))
        db.execute("INSERT INTO import_publications VALUES (?,?,?)", (import_id, repo_id, identifier))
        return {"repo_id": repo_id, "commit_id": identifier}

    def repair_import(self, subject, project_id):
        with self.store.connection(write=True) as db:
            return self.publish_import(db, subject, project_id)

    @staticmethod
    def password_hash(password, salt):
        return hashlib.scrypt(password.encode(), salt=salt, n=32768, r=8, p=1, maxmem=64 * 1024 * 1024)

    def rate_limit(self, key, maximum):
        now = time.time()
        key = hashlib.sha256(key.encode()).hexdigest()
        with self.store.connection(write=True) as db:
            db.execute("DELETE FROM auth_limits WHERE reset <= ?", (now,))
            row = db.execute("SELECT * FROM auth_limits WHERE key=?", (key,)).fetchone()
            if row and row["attempts"] >= maximum:
                raise HTTPException(429, "Too many authentication attempts", headers={"Retry-After": "900"})
            db.execute("INSERT INTO auth_limits VALUES (?,1,?) ON CONFLICT(key) DO UPDATE SET attempts=attempts+1",
                       (key, now + 900))

    def register(self, body):
        salt = secrets.token_bytes(16)
        digest = self.password_hash(body.password, salt)
        user = {"id": new_id(), "username": body.username, "name": body.name, "created_at": utc_now()}
        try:
            with self.store.connection(write=True) as db:
                db.execute("INSERT INTO users VALUES (?,?,?,?,?,?)",
                           (user["id"], body.username, body.name, salt, digest, user["created_at"]))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Username unavailable") from None
        return user

    def login(self, body):
        with self.store.connection() as db:
            user = db.execute("SELECT * FROM users WHERE username=?", (body.username.lower(),)).fetchone()
        actual = self.password_hash(body.password, user["salt"] if user else self.dummy_salt)
        if not secrets.compare_digest(actual, user["password_hash"] if user else self.dummy_hash) or user is None:
            raise HTTPException(401, "Invalid username or password")
        token = secrets.token_urlsafe(48)
        with self.store.connection(write=True) as db:
            db.execute("DELETE FROM sessions WHERE expires <= ?", (time.time(),))
            db.execute("INSERT INTO sessions VALUES (?,?,?)",
                       (hashlib.sha256(token.encode()).hexdigest(), user["id"], time.time() + SESSION_SECONDS))
        return {"access_token": token, "token_type": "bearer", "expires_in": SESSION_SECONDS,
                "user": {key: user[key] for key in ("id", "username", "name", "created_at")}}

    def resolve(self, token):
        with self.store.connection() as db:
            row = db.execute("SELECT user_id FROM sessions WHERE digest=? AND expires>?",
                             (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
        return "user:" + row["user_id"] if row else None

    def user(self, subject):
        with self.store.connection() as db:
            row = db.execute("SELECT id,username,name,created_at FROM users WHERE id=?",
                             (subject.removeprefix("user:"),)).fetchone() if subject.startswith("user:") else None
        if row is None:
            raise HTTPException(401, "A registered account is required")
        return dict(row)

    def resolve_upload(self, token, path, method):
        with self.store.connection() as db:
            row = db.execute("SELECT user_id,project_id FROM upload_tokens WHERE digest=? AND expires>?",
                             (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
        if row:
            prefix = f"/api/projects/{row['project_id']}"
            if (method == "POST" and path == prefix + "/imports" or
                method == "GET" and (path in {prefix + "/archives", prefix + "/archive", prefix + "/graph", prefix + "/repository"}
                                     or path.startswith(prefix + "/artifacts/"))):
                return "user:" + row["user_id"]
        return None

    def access(self, db, repo_id, subject, write=False):
        row = db.execute("SELECT * FROM repositories WHERE id=?", (repo_id,)).fetchone()
        require(row is not None and (row["owner"] == subject or not write and row["visibility"] == "public"),
                "Repository not found", "not_found")
        result = dict(row)
        owner = db.execute("SELECT username,name FROM users WHERE id=?", (row["owner"].removeprefix("user:"),)).fetchone()
        result["owner_username"] = owner["username"] if owner else None
        result["owner_name"] = owner["name"] if owner else None
        result["default_branch"] = "main"
        result["forks"] = db.execute("SELECT COUNT(*) FROM repositories WHERE parent_repo=? AND visibility='public'",
                                     (repo_id,)).fetchone()[0]
        return result

    def create(self, db, subject, body, parent_repo=None, parent_commit=None):
        repo_id = new_id()
        try:
            db.execute("INSERT INTO repositories VALUES (?,?,?,?,?,?,?,?,?)",
                       (repo_id, subject, body.name, body.title, body.description, body.visibility,
                        utc_now(), parent_repo, parent_commit))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Repository name already exists") from None
        db.execute("INSERT INTO repo_branches VALUES (?, 'main', NULL, ?)", (repo_id, utc_now()))
        return self.access(db, repo_id, subject)

    def branch(self, db, repo_id, name):
        row = db.execute("SELECT * FROM repo_branches WHERE repo_id=? AND name=?", (repo_id, name)).fetchone()
        require(row is not None, "Branch not found", "not_found")
        return dict(row)

    def commit(self, db, repo_id, commit_id):
        row = db.execute("SELECT * FROM repo_commits WHERE repo_id=? AND id=?", (repo_id, commit_id)).fetchone()
        require(row is not None, "Commit not found", "not_found")
        result = dict(row)
        require(hashlib.sha256(result["document"].encode()).hexdigest() == result["document_hash"],
                "Stored document is corrupt", "storage_corrupt")
        return result

    def append(self, subject, repo_id, body, raw, format_name, project_id=None, import_id=None, on_commit=None):
        with self.store.connection(write=True) as db:
            self.access(db, repo_id, subject, write=True)
            branch = self.branch(db, repo_id, body.branch)
            require(branch["head"] == (str(body.expected_head) if body.expected_head else None),
                    "Branch head changed; reload before committing", "id_conflict")
            if project_id is None and branch["head"] and format_name == "aica-semantic-graph/0.1":
                parent = self.commit(db, repo_id, branch["head"])
                before, after = json.loads(parent["document"]), json.loads(raw)
                if (before.get("source") == after.get("source") and
                        before.get("graph_id") == after.get("graph_id")):
                    project_id, import_id = parent["project_id"], parent["import_id"]
            if project_id is None and branch["head"] and format_name == "ui-module-record":
                parent = self.commit(db, repo_id, branch["head"])
                project_id, import_id = parent["project_id"], parent["import_id"]
            commit_id = new_id()
            db.execute("INSERT INTO repo_commits VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL)",
                       (commit_id, repo_id, branch["head"], subject, body.message, utc_now(), raw,
                        hashlib.sha256(raw.encode()).hexdigest(), format_name, project_id, import_id))
            db.execute("UPDATE repo_branches SET head=? WHERE repo_id=? AND name=?", (commit_id, repo_id, body.branch))
            result = self.commit(db, repo_id, commit_id)
            if on_commit:
                on_commit(db, result)
            return result

    def publication(self, parse_id):
        with self.store.connection() as db:
            row = db.execute("SELECT repo_id,commit_id FROM parse_publications WHERE parse_id=?", (parse_id,)).fetchone()
        return dict(row) if row else None

    def publish_parse(self, parse_id, subject=None):
        with self.store.connection(write=True) as db:
            row = db.execute("SELECT * FROM semantic_graph_parses WHERE id=?", (parse_id,)).fetchone()
            require(row is not None and (subject is None or subject == row["subject"]),
                    "Graph parse not found", "not_found")
            require(row["state"] == "succeeded", "Graph parsing has not succeeded")
            subject = row["subject"]
            if not subject.startswith("user:"):
                return None
            prior = db.execute("SELECT repo_id,commit_id FROM parse_publications WHERE parse_id=?", (parse_id,)).fetchone()
            if prior:
                return dict(prior)
            project = self.store.owned_project(db, subject, row["project_id"])
            binding = db.execute("SELECT repo_id FROM parsed_project_repositories WHERE project_id=?",
                                 (row["project_id"],)).fetchone()
            if binding:
                self.access(db, binding["repo_id"], subject, write=True)
                repo_id = binding["repo_id"]
                db.execute("UPDATE repositories SET visibility='private' WHERE id=?", (repo_id,))
            else:
                created = self.create(db, subject, NewRepository(
                    name="parsed-" + row["project_id"] + ("-" + new_id()[:8] if binding else ""), title=project["title"],
                    description="AI提炼的创作模块与关系，自动保存解析版本。", visibility="private"))
                repo_id = created["id"]
                db.execute("INSERT INTO parsed_project_repositories VALUES (?,?) "
                           "ON CONFLICT(project_id) DO UPDATE SET repo_id=excluded.repo_id", (row["project_id"], repo_id))
            raw, format_name = validate_document(json.loads(row["graph"]))
            parent = self.branch(db, repo_id, "main")["head"]
            identifier = new_id()
            db.execute("INSERT INTO repo_commits VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL)",
                       (identifier, repo_id, parent, subject, "自动保存 AI 提炼结果", utc_now(), raw,
                        hashlib.sha256(raw.encode()).hexdigest(), format_name, row["project_id"], row["import_id"]))
            if project["head"] == row["import_id"]:
                db.execute("UPDATE repo_branches SET head=? WHERE repo_id=? AND name='main'", (identifier, repo_id))
            db.execute("INSERT INTO parse_publications VALUES (?,?,?)", (parse_id, repo_id, identifier))
            return {"repo_id": repo_id, "commit_id": identifier}


def public_commit(commit):
    return {**{key: value for key, value in commit.items() if key not in ("document", "project_id", "import_id")},
            "has_archive": commit["project_id"] is not None}


def platform_router(principal, optional_principal):
    router = APIRouter(prefix="/api")

    def platform(request: Request):
        return request.app.state.platform

    def account(subject=Depends(principal), service=Depends(platform)):
        service.user(subject)
        return subject

    @router.post("/auth/register", status_code=201)
    def register(body: Register, request: Request, response: Response, service=Depends(platform)):
        service.rate_limit("register:" + (request.client.host if request.client else "unknown"), 10)
        response.headers["Cache-Control"] = "no-store"
        return {"status": "success", "user": service.register(body)}

    @router.post("/auth/login")
    def login(body: Login, request: Request, response: Response, service=Depends(platform)):
        service.rate_limit("login-ip:" + (request.client.host if request.client else "unknown"), 50)
        service.rate_limit("login-user:" + body.username.lower(), 10)
        response.headers["Cache-Control"] = "no-store"
        return {"status": "success", **service.login(body)}

    @router.get("/auth/me")
    def me(response: Response, subject=Depends(account), service=Depends(platform)):
        response.headers["Cache-Control"] = "no-store"
        return {"status": "success", "user": service.user(subject)}

    @router.post("/auth/logout")
    def logout(request: Request, response: Response, subject=Depends(account), service=Depends(platform)):
        token = request.headers["authorization"].split()[1]
        with service.store.connection(write=True) as db:
            db.execute("DELETE FROM sessions WHERE digest=?", (hashlib.sha256(token.encode()).hexdigest(),))
        response.headers["Cache-Control"] = "no-store"
        return {"status": "success"}

    @router.get("/projects/{project_id}/repository")
    def import_repository(project_id: UUID, subject=Depends(principal), service=Depends(platform)):
        with service.store.connection() as db:
            project = service.store.owned_project(db, subject, str(project_id))
            row = db.execute("SELECT repo_id,commit_id FROM import_publications WHERE import_id=?", (project["head"],)).fetchone()
            if row:
                service.access(db, row["repo_id"], subject, write=True)
        return {"status": "success", "import_id": project["head"], "repository":
                {"id": row["repo_id"], "commit_id": row["commit_id"], "state": "ready", "url": "#/works/" + row["repo_id"]} if row else None}

    @router.post("/projects/{project_id}/repository")
    def repair_import_repository(project_id: UUID, subject=Depends(account), service=Depends(platform)):
        service.repair_import(subject, str(project_id))
        return import_repository(project_id, subject, service)

    @router.post("/mcp/upload-tokens", status_code=201)
    def issue_upload_token(body: UploadToken, subject=Depends(account), service=Depends(platform)):
        user = service.user(subject)
        service.store.authorize(subject, str(body.project_id))
        token, identifier, expires = secrets.token_urlsafe(48), new_id(), time.time() + 30 * 86400
        with service.store.connection(write=True) as db:
            db.execute("DELETE FROM upload_tokens WHERE expires<=?", (time.time(),))
            db.execute("INSERT INTO upload_tokens VALUES (?,?,?,?,?)",
                       (identifier, hashlib.sha256(token.encode()).hexdigest(), user["id"], str(body.project_id), expires))
        return {"status": "success", "id": identifier, "access_token": token,
                "project_id": str(body.project_id), "expires_in": 30 * 86400,
                "scope": "project:imports:create"}

    @router.delete("/mcp/upload-tokens/{token_id}")
    def revoke_upload_token(token_id: UUID, subject=Depends(account), service=Depends(platform)):
        user = service.user(subject)
        with service.store.connection(write=True) as db:
            db.execute("DELETE FROM upload_tokens WHERE id=? AND user_id=?", (str(token_id), user["id"]))
        return {"status": "success"}

    @router.post("/repos", status_code=201)
    def create(body: NewRepository, subject=Depends(account), service=Depends(platform)):
        with service.store.connection(write=True) as db:
            result = service.create(db, subject, body)
        return {"status": "success", "repository": result}

    @router.get("/repos")
    def listing(scope: Literal["public", "mine"] = "public", q: str = Query("", max_length=200),
                offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                subject=Depends(optional_principal), service=Depends(platform)):
        if scope == "mine":
            if subject is None:
                raise HTTPException(401, "Platform authentication required")
            service.user(subject)
        with service.store.connection() as db:
            rows = db.execute("SELECT * FROM repositories WHERE " + ("owner=?" if scope == "mine" else "visibility=?")
                              + " AND (instr(lower(title),lower(?))>0 OR instr(lower(name),lower(?))>0) "
                              "ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
                              (subject if scope == "mine" else "public", q, q, limit, offset)).fetchall()
            results = [service.access(db, row["id"], subject) for row in rows]
        return {"status": "success", "repositories": results}

    @router.get("/repos/{repo_id}")
    def detail(repo_id: UUID, subject=Depends(optional_principal), service=Depends(platform)):
        with service.store.connection() as db:
            result = service.access(db, str(repo_id), subject)
            result["branches"] = [dict(row) for row in db.execute(
                "SELECT name,head,created_at FROM repo_branches WHERE repo_id=? ORDER BY name", (str(repo_id),))]
        return {"status": "success", "repository": result}

    @router.patch("/repos/{repo_id}/visibility")
    def visibility(repo_id: UUID, body: Visibility, subject=Depends(account), service=Depends(platform)):
        with service.store.connection(write=True) as db:
            service.access(db, str(repo_id), subject, write=True)
            db.execute("UPDATE repositories SET visibility=? WHERE id=?", (body.visibility, str(repo_id)))
        return {"status": "success"}

    @router.post("/repos/{repo_id}/branches", status_code=201)
    def branch(repo_id: UUID, body: Branch, subject=Depends(account), service=Depends(platform)):
        require(not any(part in ("", ".", "..") for part in body.name.split("/")), "Invalid branch name")
        with service.store.connection(write=True) as db:
            service.access(db, str(repo_id), subject, write=True)
            source = service.branch(db, str(repo_id), body.from_branch)
            head = str(body.from_commit) if body.from_commit else source["head"]
            if head:
                service.commit(db, str(repo_id), head)
            try:
                db.execute("INSERT INTO repo_branches VALUES (?,?,?,?)", (str(repo_id), body.name, head, utc_now()))
            except sqlite3.IntegrityError:
                raise HTTPException(409, "Branch already exists") from None
        return {"status": "success", "branch": {"name": body.name, "head": head}}

    @router.get("/repos/{repo_id}/branches")
    def branches(repo_id: UUID, subject=Depends(optional_principal), service=Depends(platform)):
        with service.store.connection() as db:
            service.access(db, str(repo_id), subject)
            rows = db.execute("SELECT name,head,created_at FROM repo_branches WHERE repo_id=? ORDER BY name", (str(repo_id),))
            return {"status": "success", "branches": [dict(row) for row in rows]}

    @router.post("/repos/{repo_id}/commits", status_code=201)
    def commit(repo_id: UUID, body: Commit, subject=Depends(account), service=Depends(platform)):
        with service.store.connection() as db:
            service.access(db, str(repo_id), subject, write=True)
        raw, format_name = validate_document(body.document)
        result = service.append(subject, str(repo_id), body, raw, format_name)
        return {"status": "success", "commit": public_commit(result)}

    @router.post("/repos/{repo_id}/archive-commits", status_code=201)
    def archive_commit(repo_id: UUID, body: ArchiveCommit, subject=Depends(account), service=Depends(platform)):
        with service.store.connection() as db:
            service.access(db, str(repo_id), subject, write=True)
        snapshot = service.store.snapshot(subject, str(body.project_id), str(body.import_id))
        raw, format_name = validate_document(snapshot["archive"])
        result = service.append(subject, str(repo_id), body, raw, format_name, str(body.project_id), str(body.import_id))
        return {"status": "success", "commit": public_commit(result)}

    @router.post("/repos/{repo_id}/semantic-commits", status_code=201)
    def semantic_commit(repo_id: UUID, body: SemanticCommit, request: Request,
                        subject=Depends(account), service=Depends(platform)):
        with service.store.connection() as db:
            service.access(db, str(repo_id), subject, write=True)
        graph = request.app.state.graphs.graph(subject, str(body.project_id), body.graph_id, body.revision_id)
        require(graph.source.import_id == str(body.import_id), "Semantic Graph source differs from selected import")
        raw, format_name = validate_document(graph.model_dump())
        result = service.append(subject, str(repo_id), body, raw, format_name, str(body.project_id), str(body.import_id))
        return {"status": "success", "commit": public_commit(result)}

    @router.get("/repos/{repo_id}/commits")
    def history(repo_id: UUID, branch: str = Query("main", max_length=80),
                offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                subject=Depends(optional_principal), service=Depends(platform)):
        with service.store.connection() as db:
            service.access(db, str(repo_id), subject)
            head = service.branch(db, str(repo_id), branch)["head"]
            rows = db.execute("WITH RECURSIVE history AS (SELECT *,0 AS depth FROM repo_commits WHERE id=? AND repo_id=? "
                              "UNION ALL SELECT c.*,h.depth+1 FROM repo_commits c JOIN history h ON c.id=h.parent "
                              "WHERE c.repo_id=?) SELECT * FROM history ORDER BY depth LIMIT ? OFFSET ?",
                              (head, str(repo_id), str(repo_id), limit, offset)).fetchall()
        return {"status": "success", "commits": [public_commit(dict(row)) for row in rows]}

    @router.get("/repos/{repo_id}/graph")
    def graph(repo_id: UUID, branch: str = Query("main", max_length=80), commit_id: UUID | None = None,
              subject=Depends(optional_principal), service=Depends(platform)):
        with service.store.connection() as db:
            service.access(db, str(repo_id), subject)
            head = str(commit_id) if commit_id else service.branch(db, str(repo_id), branch)["head"]
            result = service.commit(db, str(repo_id), head)
        document = json.loads(result["document"])
        response = {"status": "success", "commit": public_commit(result), "document": document}
        if "aica_version" in document or document.get("format") == "aica-ui-module-record" and result["project_id"]:
            from .process_view import repository_process_view

            response["process_graph"] = repository_process_view(service, result, document)
        return response

    @router.get("/repos/{repo_id}/modules/{node_id}/materials")
    def module_materials(repo_id: UUID, node_id: str, commit_id: UUID,
                         subject=Depends(optional_principal), service=Depends(platform)):
        from .module_content import module_content

        with service.store.connection() as db:
            service.access(db, str(repo_id), subject)
            commit = service.commit(db, str(repo_id), str(commit_id))
        return {"status": "success", **module_content(service, commit, node_id)}

    @router.get("/repos/{repo_id}/artifacts/{artifact_id}")
    def artifact(repo_id: UUID, artifact_id: str, commit_id: UUID, subject=Depends(optional_principal), service=Depends(platform)):
        from fastapi.responses import FileResponse

        with service.store.connection() as db:
            service.access(db, str(repo_id), subject)
            result = service.commit(db, str(repo_id), str(commit_id))
            require(result["project_id"] is not None, "No bundled artifact in this version", "not_found")
            project = db.execute("SELECT subject FROM projects WHERE id=?", (result["project_id"],)).fetchone()
            require(project is not None, "Artifact source missing", "storage_corrupt")
        snapshot = service.store.media_snapshot(project["subject"], result["project_id"], result["import_id"])
        from .repo_media import effective_images, media_path, media_response
        current = effective_images(json.loads(result["document"]), snapshot["archive"]["artifacts"]).get(artifact_id)
        if current:
            record, path = media_path(service, str(repo_id), result, current)
            return media_response(record, path, False)
        record, path = service.store.artifact_in_snapshot(snapshot, artifact_id)
        mime = record["mime_type"]
        inline = mime in {"image/png", "image/jpeg", "image/webp", "image/gif", "image/avif"}
        return FileResponse(path, media_type=mime if inline else "application/octet-stream",
                            filename="artifact-" + record["sha256"], content_disposition_type="inline" if inline else "attachment",
                            headers={"X-AiCA-SHA256": record["sha256"], "X-Content-Type-Options": "nosniff",
                                     "Content-Security-Policy": "sandbox; default-src 'none'", "Cache-Control": "private, no-store"})

    @router.post("/repos/{repo_id}/forks", status_code=201)
    def fork(repo_id: UUID, body: Fork, subject=Depends(account), service=Depends(platform)):
        with service.store.connection(write=True) as db:
            service.access(db, str(repo_id), subject)
            head = service.branch(db, str(repo_id), body.branch)["head"]
            require(head is not None, "Cannot fork an empty branch", "id_conflict")
            source = service.commit(db, str(repo_id), head)
            result = service.create(db, subject, body, str(repo_id), head)
            fork_commit = new_id()
            db.execute("INSERT INTO repo_commits VALUES (?,?,NULL,?,?,?,?,?,?,?,?,?)",
                       (fork_commit, result["id"], subject, "Fork: " + body.branch, utc_now(), source["document"],
                        source["document_hash"], source["format"], source["project_id"], source["import_id"], source["id"]))
            db.execute("UPDATE repo_branches SET head=? WHERE repo_id=? AND name='main'", (fork_commit, result["id"]))
            result["head"] = fork_commit
        return {"status": "success", "repository": result}

    from .repo_media import attach_repo_media
    attach_repo_media(router, account, optional_principal, platform)
    return router
