import tempfile
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient
from cairn.server import db, auth_db
from cairn.server.routers import auth

tmp = Path(tempfile.mkdtemp()) / "t.db"
db.configure(tmp)
auth_db.configure_auth_db()
app = FastAPI()
app.include_router(auth.router)
c = TestClient(app)

results = []
r = c.post("/api/auth/register", json={"username": "Alice", "password": "hunter2pw"})
results.append(("register_valid", r.status_code, 201))
results.append(("dup_caseinsensitive", c.post("/api/auth/register", json={"username": "aLiCe", "password": "another8x"}).status_code, 409))
results.append(("short_pw", c.post("/api/auth/register", json={"username": "bobby", "password": "short"}).status_code, 422))
results.append(("bad_username", c.post("/api/auth/register", json={"username": "ab", "password": "validpass8"}).status_code, 422))

with open("/Users/rabbit/Desktop/Cairn-0.2.1/cairn/_tmp_result.txt", "w") as f:
    allok = True
    for name, got, want in results:
        ok = got == want
        allok = allok and ok
        f.write(f"{name}: got={got} want={want} {'OK' if ok else 'FAIL'}\n")
    f.write("ALL_OK\n" if allok else "SOME_FAILED\n")
