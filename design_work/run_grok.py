# -*- coding: utf-8 -*-
"""运行 grok CLI 对单段提示词做单轮审查；捕获 bytes 双解码，存档输出。
用法: python run_grok.py <prompt_file> <out_file>
"""
import sys, io, subprocess

GROK = r"C:/Users/nirvana/.grok/downloads/grok-windows-x86_64.exe"

def decode(b):
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return b.decode(enc)
        except Exception:
            continue
    return b.decode("utf-8", "replace")

def main():
    prompt_file, out_file = sys.argv[1], sys.argv[2]
    with io.open(prompt_file, "r", encoding="utf-8") as f:
        prompt = f.read()
    cmd = [GROK, "-p", prompt, "--max-turns", "3", "--disable-web-search", "--verbatim"]
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=480)
        out = decode(p.stdout)
        err = decode(p.stderr)
        rc = p.returncode
    except subprocess.TimeoutExpired as e:
        out = decode(e.stdout or b"")
        err = "TIMEOUT after 480s\n" + decode(e.stderr or b"")
        rc = -1
    full = "==== RETURNCODE %s ====\n==== STDOUT ====\n%s\n==== STDERR ====\n%s\n" % (rc, out, err)
    with io.open(out_file, "w", encoding="utf-8") as f:
        f.write(full)
    # 控制台也打印（供实时观察）
    sys.stdout.buffer.write(full.encode("utf-8", "replace"))

if __name__ == "__main__":
    main()
