"""下载固定版本原 Java 算法，在本地 JDK 中生成并验证双向 PNG 互通向量。

不执行上游 Gradle 或其他构建脚本；下载、类文件和图片仅存放于忽略的验证目录。
"""

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import urllib.request

from PIL import Image

from confusing_images import core, upstream

ROOT = Path(__file__).resolve().parents[1]
REVISION = "b573e0051d375a7a4c7ccfe7d7e385997b52348b"
BASE = f"https://raw.githubusercontent.com/2195517546/ObfuscationUtils/{REVISION}/"
CLASSES = (
    "TomatoObfuscation", "BlockObfuscation", "RowPixelObfuscation", "PerPixelObfuscation",
    "PicEncryptRowObfuscation", "PicEncryptRowColumnObfuscation", "SortObfuscation", "RandomObfuscation",
)


def find_jdk() -> Path:
    """优先使用现有 JDK，不安装或修改系统 Java 环境。"""
    javac = shutil.which("javac")
    if javac:
        return Path(javac).resolve().parent
    home = os.environ.get("JAVA_HOME")
    if home and (Path(home) / "bin" / "javac.exe").exists():
        return Path(home) / "bin"
    candidates = sorted((Path.home() / ".jdks").glob("*/bin/javac.exe"))
    if candidates:
        return candidates[-1].parent
    raise RuntimeError("未找到现有 JDK，请通过 --jdk-bin 指定；此工具不会自动安装 Java。")


def download_source(relative: str, destination: Path) -> tuple[str, str]:
    """下载已审核的固定版本文件，保留其内容和 SHA256。"""
    output = destination / relative
    output.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(BASE + relative, timeout=90) as response:
        content = response.read()
    output.write_bytes(content)
    return relative, hashlib.sha256(content).hexdigest()


def rgba_bytes(path: Path) -> bytes:
    """统一读取 RGBA 原始像素，避免不同编码器的 PNG 字节差异。"""
    with Image.open(path) as image:
        return image.convert("RGBA").tobytes()


def run_case(java: Path, classes: Path, folder: Path, case: dict) -> dict:
    """验证 Java→Python、Python→Java，且对比混淆像素而非仅验证自往返。"""
    directory = folder / case["id"]
    command = [str(java), "-Dfile.encoding=UTF-8", "-cp", str(classes), "ReferenceMain"]
    encoded_key = base64.b64encode(case["key"].encode("utf-8")).decode("ascii")
    arguments = ["generate", case["algorithm"], encoded_key, case["mode"], str(case["width"]), str(case["height"]), str(directory)]
    subprocess.run(command + arguments, check=True, capture_output=True, timeout=60)
    source = core.load_image(directory / "source.png")
    if case["mode"] == "RGB":
        source = source.convert("RGB")
    encrypted = core.process_compatible(source, case["key"], case["algorithm"])
    python_path = directory / "python-encrypted.png"
    core.save_png(encrypted, python_path)
    restored = core.process_compatible(core.load_image(directory / "java-encrypted.png"), case["key"], case["algorithm"], True)
    restored_path = directory / "python-restored.png"
    core.save_png(restored, restored_path)
    decoded_by_java = directory / "java-from-python.png"
    subprocess.run(command + ["decrypt", case["algorithm"], encoded_key, str(python_path), str(decoded_by_java)], check=True, capture_output=True, timeout=60)
    java_cipher = rgba_bytes(directory / "java-encrypted.png")
    java_restore = rgba_bytes(directory / "java-restored.png")
    result = case.copy()
    result.update(
        cipher_equal=rgba_bytes(python_path) == java_cipher,
        python_restore_equal=rgba_bytes(restored_path) == java_restore,
        java_restore_equal=rgba_bytes(decoded_by_java) == java_restore,
        encrypted_size=list(encrypted.size),
        cipher_sha256=hashlib.sha256(java_cipher).hexdigest(),
        restored_sha256=hashlib.sha256(java_restore).hexdigest(),
    )
    if not all(result[name] for name in ("cipher_equal", "python_restore_equal", "java_restore_equal")):
        raise AssertionError(f"Java 对照失败：{result}")
    return result


def main() -> None:
    """在本地验证目录编译最小夹具，写出可追溯的原项目互通报告。"""
    parser = argparse.ArgumentParser(description="ObfuscationUtils 原 Java 源码双向对照")
    parser.add_argument("--jdk-bin", type=Path)
    arguments = parser.parse_args()
    jdk = arguments.jdk_bin or find_jdk()
    folder = ROOT / "doc" / "validation" / "upstream-java"
    sources = folder / "sources"
    classes = folder / "classes"
    classes.mkdir(parents=True, exist_ok=True)
    prefix = "src/main/java/com/uiloalxise/"
    relatives = [prefix + "utils/" + name + ".java" for name in CLASSES]
    relatives += [prefix + "utils/base/" + name + ".java" for name in ("ImageObfuscation", "PixelObfuscation", "PicEncryptObfuscation")]
    relatives += [prefix + "Util.java", "LICENSE"]
    with ThreadPoolExecutor(max_workers=6) as executor:
        hashes = dict(executor.map(lambda relative: download_source(relative, sources), relatives))
    java_sources = list(sources.rglob("*.java")) + list((ROOT / "tools" / "java_reference").glob("*.java"))
    compiled = subprocess.run(
        [str(jdk / "javac.exe"), "-encoding", "UTF-8", "-d", str(classes)] + [str(path) for path in java_sources],
        check=True, capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    cases = []
    for algorithm in upstream.ALGORITHM_IDS:
        key = "0.618" if algorithm in upstream.NUMERIC_ALGORITHMS else "中文密钥😀"
        for mode in ("RGB", "RGBA"):
            cases.append(dict(id=f"{algorithm}-{mode}", algorithm=algorithm, key=key, mode=mode, width=17, height=13))
    for index, (algorithm, key, width, height) in enumerate((
        ("tomato", "-1.25", 31, 48), ("tomato", "114514", 128, 96),
        ("tomato", "0.618", 1, 31), ("tomato", "0.618", 37, 1),
        ("pic-row-column", "0.1145", 61, 47), ("pic-row-column", "0.618", 1, 17),
        ("random", "114514", 33, 16), ("random", "-9223372036854775808", 61, 9),
        ("random", "9223372036854775808", 256, 7), ("block", "key", 64, 32),
    )):
        cases.append(dict(id=f"extra-{index}", algorithm=algorithm, key=key, mode="RGBA", width=width, height=height))
    results = []
    for case in cases:
        results.append(run_case(jdk / "java.exe", classes, folder, case))
        # 仅打印公开夹具名称，不记录用户图片或用户密钥。
        print(case["id"] + ": Java/Python PNG 双向一致", flush=True)
    version = subprocess.run([str(jdk / "java.exe"), "-version"], capture_output=True, text=True, encoding="utf-8", check=True)
    report = dict(upstream_revision=REVISION, java_version=version.stderr.strip(), source_hashes=hashes, cases=results)
    output = folder / "verification.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(results)} 组双向对照全部通过：{output}")


if __name__ == "__main__":
    main()
