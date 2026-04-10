# -*- coding: utf-8 -*-
import sys
import requests, zipfile, io, os, platform, shutil
from pathlib import Path
import urllib3
import logging

if sys.platform == "win32":
    import winreg

logger = logging.getLogger("LearningPilot")
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def get_local_chrome_version():
    """透過 Windows 登錄檔獲取 Chrome 版本（僅 Windows）"""
    if sys.platform != "win32":
        return "120.0.0.0"
    reg_path = r"SOFTWARE\Google\Chrome\BLBeacon"
    try:
        # 先嘗試 HKEY_CURRENT_USER
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path) as key:
            return winreg.QueryValueEx(key, "version")[0]
    except OSError:
        try:
            # 再嘗試 HKEY_LOCAL_MACHINE
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_path) as key:
                return winreg.QueryValueEx(key, "version")[0]
        except OSError:
            return "120.0.0.0"


def download_best_chromedriver(folder_name="drivers"):
    """自動下載並匹配 Windows 版 ChromeDriver，優先找完全相同版本"""
    # 打包成 exe 時用 exe 所在目錄；一般執行時用腳本所在目錄的上層
    import sys

    if getattr(sys, "frozen", False):
        base_path = os.path.dirname(sys.executable)  # exe 所在目錄
    else:
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    target_folder = os.path.join(base_path, folder_name)

    try:
        driver_file = os.path.join(target_folder, "chromedriver.exe")

        if os.path.exists(driver_file):
            logger.info(f"已存在 driver，直接使用: {driver_file}")
            return driver_file

        os.makedirs(target_folder, exist_ok=True)
    except Exception as e:
        logger.warning(f"清理驅動目錄時發生警告: {e}")

    local_version = get_local_chrome_version()
    parts = local_version.split(".")
    prefix3 = ".".join(parts[:3])
    logger.info(f"偵測到本機 Chrome 版本: {local_version}")

    def extract_driver(zip_url):
        r = requests.get(zip_url, verify=True, timeout=30)
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            z.extractall(target_folder)
        driver = next(Path(target_folder).rglob("chromedriver.exe"), None)
        if not driver:
            raise RuntimeError("解壓縮後找不到 chromedriver.exe")

        final_path = os.path.join(target_folder, "chromedriver.exe")
        shutil.move(str(driver), final_path)

        logger.info(f"驅動配置完成: {final_path}")
        return final_path

    # 策略一：known-good-versions，優先找完全相同版本
    try:
        known_url = "https://googlechromelabs.github.io/chrome-for-testing/known-good-versions-with-downloads.json"
        logger.info(f"正在尋找與 {local_version} 完全匹配的驅動...")
        resp = requests.get(known_url, verify=True, timeout=15)
        versions = resp.json().get("versions", [])

        exact = next((v for v in versions if v["version"] == local_version), None)
        if not exact:
            same_build = [v for v in versions if v["version"].startswith(prefix3 + ".")]
            if same_build:
                local_patch = int(parts[3]) if len(parts) > 3 else 0
                exact = min(
                    same_build,
                    key=lambda v: abs(int(v["version"].split(".")[3]) - local_patch),
                )
                logger.info(f"找不到完全匹配，改用最接近版本: {exact['version']}")

        if exact:
            url = next(
                (
                    d["url"]
                    for d in exact["downloads"].get("chromedriver", [])
                    if d["platform"] == "win64"
                ),
                None,
            )
            if url:
                logger.info(f"正在下載驅動程式: {url}")
                return extract_driver(url)
    except Exception as e:
        logger.warning(f"精確版本查詢失敗，改用 patch API: {e}")

    # 策略二：patch API fallback
    try:
        json_url = "https://raw.githubusercontent.com/GoogleChromeLabs/chrome-for-testing/refs/heads/gh-pages/latest-patch-versions-per-build-with-downloads.json"
        logger.info(f"正在從 GitHub 獲取匹配 {prefix3} 的驅動資訊...")
        resp = requests.get(json_url, verify=True, timeout=10)
        data = resp.json()
        entry = data["builds"][prefix3]
        url = next(
            d["url"]
            for d in entry["downloads"]["chromedriver"]
            if d["platform"] == "win64"
        )
        logger.info(f"正在下載驅動程式: {url}")
        return extract_driver(url)
    except Exception as e:
        logger.error(f"Driver 下載或配置故障: {e}")
        fallback = os.path.join(target_folder, "chromedriver.exe")
        if os.path.exists(fallback):
            return fallback
        raise e


if __name__ == "__main__":
    # 測試執行
    logging.basicConfig(level=logging.INFO)
    path = download_best_chromedriver()
    print(f"Result: {path}")
