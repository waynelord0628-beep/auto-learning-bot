import logging
import unittest
from unittest.mock import patch
from utils import ecpa_login as login
from selenium.common.exceptions import NoAlertPresentException, UnexpectedAlertPresentException


class Element:
    def __init__(self, driver, name): self.driver,self.name=driver,name
    def clear(self): pass
    def send_keys(self, value): pass
    def is_displayed(self): return True
    screenshot_as_png=b'fake-image'
    def get_attribute(self, name): return str(self.driver.images)
    def click(self):
        if self.name=='captchaLogin': self.driver.images+=1;return
        self.driver.submits+=1
        result=self.driver.results.pop(0)
        if result=='success': self.driver.current_url='https://elearn.hrd.gov.tw/mooc/index.php'
        elif result=='platform_error':
            self.driver.current_url='https://elearn.hrd.gov.tw/sso_home.php'
            self.driver.platform_error=True
        else:self.driver.message=result


class Driver:
    def __init__(self, results, captcha=True):
        self.results=list(results);self.captcha=captcha;self.submits=0;self.images=0;self.message=None
        self.switch_to=self
    def get(self,url):self.current_url=url;self.platform_error=False
    def find_element(self,by,name):return Element(self,name)
    def find_elements(self,by,name):
        if by=='xpath':return [Element(self,name)] if getattr(self,'logged_in',True) else []
        return [Element(self,name)] if self.captcha else []
    def execute_script(self,*args):
        if 'No any school bind' in args[0]:return self.platform_error
        if "button').click()" in args[0]:Element(self,'submit').click()
        return True
    @property
    def alert(self):
        if self.message is None:raise NoAlertPresentException()
        return self
    @property
    def text(self):return self.message
    def accept(self):self.message=None


class LoginTests(unittest.TestCase):
    def run_login(self,d,ocr=lambda b:'12345',running=lambda:True):
        return login.submit_ecpa_login(d,login.WebDriverWait(d,.1),'https://ecpa.dgpa.gov.tw/login',
            'test-account','not-a-real-password',running,logging.getLogger('test'),recognize=ocr)
    def test_captcha_rejection_then_success(self):
        d=Driver(['驗證碼錯誤','success']);self.assertTrue(self.run_login(d));self.assertEqual(d.submits,2)
    def test_password_error_stops(self):
        d=Driver(['帳號或密碼錯誤','success']);self.assertFalse(self.run_login(d));self.assertEqual(d.submits,1)
    def test_alert_dismissed_during_url_read(self):
        class RacingDriver(Driver):
            @property
            def current_url(self):
                if self.message is not None:
                    message,self.message=self.message,None
                    raise UnexpectedAlertPresentException(alert_text=message)
                return self.url
            @current_url.setter
            def current_url(self,value):self.url=value
        d=RacingDriver(['驗證碼錯誤','success'])
        self.assertTrue(self.run_login(d));self.assertEqual(d.submits,2)
        d=RacingDriver(['帳號或密碼錯誤','success'])
        self.assertFalse(self.run_login(d));self.assertEqual(d.submits,1)
    def test_bad_ocr_never_submits_credentials(self):
        d=Driver([]);self.assertFalse(self.run_login(d,lambda b:''));self.assertEqual(d.submits,0);self.assertEqual(d.images,2)
    def test_platform_error_recovers_once_with_total_attempt_limit(self):
        with patch.object(login.time,'sleep'):
            d=Driver(['platform_error','success'])
            self.assertTrue(self.run_login(d));self.assertEqual(d.submits,2)
            d=Driver(['platform_error','platform_error','success'])
            self.assertFalse(self.run_login(d));self.assertEqual(d.submits,2)
    def test_three_captcha_failures_stop(self):
        d=Driver(['驗證碼錯誤']*3);self.assertFalse(self.run_login(d));self.assertEqual(d.submits,3)
    def test_stop_and_no_captcha_compatibility(self):
        d=Driver([]);self.assertFalse(self.run_login(d,running=lambda:False));self.assertEqual(d.submits,0)
        self.assertTrue(self.run_login(Driver(['success'],captcha=False)))
    def test_destination_is_exact_host(self):
        self.assertFalse(login._destination('https://elearn.hrd.gov.tw.attacker.example/'))
        self.assertFalse(login._destination('https://elearn.hrd.gov.tw/login.php'))
        self.assertFalse(login._destination('https://elearn.hrd.gov.tw/sso_verify.php'))
        self.assertTrue(login._destination('https://elearn.hrd.gov.tw/mooc/index.php'))
    def test_anonymous_platform_page_is_not_authenticated(self):
        d=Driver([]);d.current_url='https://elearn.hrd.gov.tw/mooc/index.php'
        d.logged_in=False
        self.assertFalse(login.authenticated(d))
        d.logged_in=True
        self.assertTrue(login.authenticated(d))
        self.assertFalse(login.authenticated(d,require_stat=True))
        d.current_url='https://elearn.hrd.gov.tw/mooc/user/learn_stat.php'
        self.assertTrue(login.authenticated(d,require_stat=True))
    def test_five_characters_preserve_letters(self):
        from unittest.mock import Mock
        with patch.object(login,'_ocr',Mock(classification=lambda _: 'Ab123')):
            self.assertEqual(login.recognize_captcha(b'x'),'Ab123')
        with patch.object(login,'_ocr',Mock(classification=lambda _: '1234')):
            self.assertEqual(login.recognize_captcha(b'x'),'')


if __name__=='__main__':unittest.main()
