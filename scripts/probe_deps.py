import sys
import time

sys.path.insert(0, "src")
import deps as dm

root = r"C:\Users\11965\AppData\Local\Temp\kornia-inspect"
for i in (1, 2):
    t = time.time()
    r = dm.analyze_deps(root)
    print("run%d: %.1fs status=%s oldest=%s days=%s got=%d/%d" % (
        i, time.time() - t, r["status"], r.get("oldest_pkg"), r.get("oldest_days"),
        len(r["items"]), r["count"]))
