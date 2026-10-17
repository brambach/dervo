# Metadata only: paths, counts, mtimes. No message content is read.
import os, time, collections
from datetime import datetime
root = os.path.expanduser("~/.claude/projects")
now = time.time()
projs = []
for d in os.scandir(root):
    if not d.is_dir(): continue
    files = [f for f in os.scandir(d.path) if f.name.endswith(".jsonl")]
    if not files: continue
    mtimes = [f.stat().st_mtime for f in files]
    size = sum(f.stat().st_size for f in files)
    projs.append(dict(name=d.name, n=len(files), last=max(mtimes),
                      first=min(mtimes), size=size))
projs.sort(key=lambda p: -p["last"])
tot_s = sum(p["n"] for p in projs)
print(f"{len(projs)} project folders, {tot_s} transcripts\n")

buckets = collections.Counter()
for p in projs:
    age = (now - p["last"]) / 86400
    b = ("today" if age < 1 else "this week" if age < 7 else "this month" if age < 31
         else "3 months" if age < 92 else "older")
    buckets[b] += 1
print("projects by last activity:")
for b in ("today","this week","this month","3 months","older"):
    print(f"  {b:11} {buckets[b]:4}")

print(f"\nsessions per project: max {max(p['n'] for p in projs)}, "
      f"median {sorted(p['n'] for p in projs)[len(projs)//2]}, "
      f"{sum(1 for p in projs if p['n']==1)} projects with exactly 1")

print("\ntop 12 by last activity:")
for p in projs[:12]:
    path = p["name"].replace("-", "/")
    print(f"  {datetime.fromtimestamp(p['last']):%d %b %H:%M}  {p['n']:4} sess  {path[:72]}")

print("\n12 oldest-touched (the 'you left this in April' candidates):")
for p in projs[-12:]:
    path = p["name"].replace("-", "/")
    print(f"  {datetime.fromtimestamp(p['last']):%d %b %Y}  {p['n']:4} sess  {path[:72]}")
