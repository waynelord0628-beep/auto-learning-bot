"""eCPA image CAPTCHA login; uses the same local OCR dependency as Taipei E-da."""
import re
import time
from urllib.parse import urlparse

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoAlertPresentException, TimeoutException, UnexpectedAlertPresentException

_ocr = None


def recognize_captcha(image_bytes):
    global _ocr
    if _ocr is None:
        import ddddocr
        _ocr = ddddocr.DdddOcr(show_ad=False)
    # Preserve letters: eCPA's five-character field is not Taipei's four-digit field.
    value = re.sub(r"\s+", "", _ocr.classification(image_bytes))
    return value if re.fullmatch(r"[A-Za-z0-9]{5}", value) else ""


def _destination(url):
    parsed = urlparse(url)
    return (parsed.scheme == 'https' and parsed.hostname == 'elearn.hrd.gov.tw'
            and 'login' not in parsed.path.lower()
            and parsed.path not in ('/sso_verify.php', '/sso_home.php'))


def _platform_error(driver):
    parsed = urlparse(driver.current_url)
    if parsed.scheme != 'https' or parsed.hostname != 'elearn.hrd.gov.tw':
        return False
    return driver.execute_script("return !!document.body && document.body.innerText.includes('No any school bind the server_name');")


def authenticated(driver, require_stat=False):
    """Anonymous platform pages and anonymous session cookies are not a login."""
    url = driver.current_url
    if not _destination(url):
        return False
    if require_stat and urlparse(url).path != '/mooc/user/learn_stat.php':
        return False
    return any(element.is_displayed() for element in driver.find_elements(
        By.XPATH, "//*[self::a or self::button][contains(normalize-space(.), '登出')]"))


def submit_ecpa_login(driver, wait, url, account, password, running, logger,
                      recognize=recognize_captcha, attempts=3):
    """Return only after SSO redirect; never retry an unknown/password failure."""
    driver.get(url)
    wait.until(EC.presence_of_element_located((By.ID, 'aliasid')))
    platform_retries = 0
    for attempt in range(min(attempts, 3)):
        if not running():
            return False
        for field, value in [('aliasid', account), ('pas', password)]:
            element = driver.find_element(By.ID, field)
            element.clear()
            element.send_keys(value)
        fields = driver.find_elements(By.ID, 'txtCaptchaLogin')
        has_captcha = bool(fields and fields[0].is_displayed())
        if has_captcha:
            wait.until(lambda d: d.execute_script(
                "const i=document.getElementById('captchaLogin'); return !!(i && i.complete && i.naturalWidth>0);"))
            image = driver.find_element(By.ID, 'captchaLogin')
            # Screenshot the displayed image, not a second HTTP request which may replace the session code.
            code = recognize(image.screenshot_as_png)
            if not running():
                return False
            if not code:
                logger.info('eCPA 驗證碼辨識不足五碼，重新讀取（%s/3）', attempt + 1)
                if attempt < 2:
                    _refresh(driver, wait)
                continue
            fields[0].clear()
            fields[0].send_keys(code)
        logger.info('eCPA 正在驗證登入（%s/3）', attempt + 1)
        # Keep the existing application's submit mechanism; native click can wait
        # on the complete SSO navigation before our cancellable wait gets control.
        driver.execute_script("document.querySelector('#idarea button').click();")
        logger.info('eCPA 登入已送出，等待轉入學習平臺。')
        next_progress = time.monotonic() + 15

        def outcome(d):
            nonlocal next_progress
            if not running():
                return 'stopped'
            if time.monotonic() >= next_progress:
                logger.info('eCPA 已送出登入，等待網站回應；不重複送出帳密。')
                next_progress = time.monotonic() + 15
            try:
                if authenticated(d):
                    return 'success'
                if _platform_error(d):
                    return 'platform_error'
                alert = d.switch_to.alert
                message = alert.text or ''
                alert.accept()
            except UnexpectedAlertPresentException as exc:
                # Chrome may dismiss an alert while reading current_url. Keep the
                # reported rejection so it is not mistaken for a navigation error.
                message = exc.alert_text or ''
                try:
                    d.switch_to.alert.accept()
                except NoAlertPresentException:
                    pass
            except NoAlertPresentException:
                return False
            # Only a CAPTCHA rejection permits another credential submission.
            if '驗證碼' in message and not any(x in message for x in ('密碼錯', '帳號錯', '鎖定', '停用')):
                return 'captcha'
            return 'rejected'

        try:
            result = WebDriverWait(driver, 120, poll_frequency=.3).until(outcome)
        except TimeoutException:
            logger.error('eCPA 未完成登入，請查看登入頁提示；為避免鎖定帳號，不自動重送。')
            return False
        if result == 'success':
            return True
        if result == 'platform_error':
            logger.warning('學習平臺回傳站台設定錯誤，並非驗證碼錯誤。')
            if platform_retries or attempt >= min(attempts, 3) - 1:
                logger.error('學習平臺仍無法交接登入，請稍後重試。')
                return False
            platform_retries += 1
            logger.info('等待五秒後重新建立登入，最多恢復一次。')
            for _ in range(10):
                if not running():
                    return False
                time.sleep(.5)
            driver.get(url)
            wait.until(EC.presence_of_element_located((By.ID, 'aliasid')))
            continue
        if result != 'captcha' or not has_captcha:
            logger.error('eCPA 登入未通過或已停止，請檢查帳密及網站的身分驗證提示。')
            return False
        logger.info('eCPA 驗證碼未通過，換圖重試。')
        if attempt < 2:
            _refresh(driver, wait)
    logger.error('eCPA 驗證碼已嘗試三次，請稍後重試或手動登入。')
    return False


def _refresh(driver, wait):
    image = driver.find_element(By.ID, 'captchaLogin')
    old = image.get_attribute('src')
    image.click()
    wait.until(lambda d: d.find_element(By.ID, 'captchaLogin').get_attribute('src') != old)
