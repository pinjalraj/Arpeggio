"""Observational probes for Tests C and D2. Run ONLY inside the disposable test container:

    docker exec rlenv-sectest python -B /tests/docker_probe_c_d.py

It talks only to the sandbox's own API on localhost and reads /proc. It does not stop,
signal or replace the sandbox server.

C  (precondition only): does agent code (run_python) run as the same user as the server,
   and can it reach the server's HTTP port?
D2: does a harmless `sleep 30` started by agent code, and a /tmp file, survive /reset?
"""
import json
import urllib.request

URL = "http://localhost:8000"


def post(path, body):
    request = urllib.request.Request(URL + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())


def run_python(code):
    return post("/tool", {"name": "run_python", "args": {"code": code}})["output"]


C_CODE = r'''
import os, urllib.request
server = []
for pid in filter(str.isdigit, os.listdir("/proc")):
    try:
        cmd = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode()
        if "server.py" in cmd:
            uid = [l.split()[1] for l in open(f"/proc/{pid}/status") if l.startswith("Uid:")][0]
            server.append((pid, uid))
    except OSError:
        pass
try:
    health = urllib.request.urlopen("http://localhost:8000/health", timeout=3).status
except Exception as exc:
    health = type(exc).__name__
print(f"agent_uid={os.getuid()} server_procs={server} health_from_agent={health}")
'''

D_START = r'''
import subprocess
p = subprocess.Popen(["sleep", "30"], start_new_session=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
open("/tmp/rlsec_marker.txt", "w").write("episode 1")
print(p.pid)
'''

D_CHECK = r'''
import os
pid = {pid}
state = "absent"
if os.path.exists(f"/proc/{{pid}}"):
    state = open(f"/proc/{{pid}}/stat").read().split()[2]
print(f"sleep_pid={{pid}} after_reset_state={{state}} tmp_marker_exists={{os.path.exists('/tmp/rlsec_marker.txt')}}")
'''


def main():
    post("/reset", {"files": {"a.py": "x = 1\n"}})
    print("C:", run_python(C_CODE).strip())

    post("/reset", {"files": {"a.py": "x = 1\n"}})
    pid = int(run_python(D_START).split()[0])
    post("/reset", {"files": {"a.py": "x = 1\n"}})
    print("D2:", run_python(D_CHECK.format(pid=pid)).strip())


if __name__ == "__main__":
    main()
