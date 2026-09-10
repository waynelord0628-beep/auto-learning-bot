import ast
import logging
from pathlib import Path
import random
import re
import unicodedata
import unittest


ROOT = Path(__file__).resolve().parents[1]

def extract(filename):
    source = (ROOT / 'app.py').read_text(encoding='utf-8')
    if filename != 'app.py':
        source = source.replace('m = re.fullmatch(r"\\s*(\\d+)(?:[.、)）:：]\\s*[^\\d\\s].*)?\\s*", ans_norm)', 'm = re.search(r"(?<!\\d)(\\d+)(?!\\d)", ans_norm)')
    tree = ast.parse(source)
    exam = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "auto_exam")
    branch = next(n for n in ast.walk(exam) if isinstance(n, ast.If)
                  and isinstance(n.test, ast.Name) and n.test.id == 'radios')
    fn = ast.parse('def select(ans, option_texts, radio_values=None):\n    radios = [None] * len(option_texts)\n    radio_values = radio_values or []\n    is_true_false = len(radios) == 2 and set(radio_values) == {"T", "F"}\n').body[0]
    fn.body += branch.body[:2] + [ast.Return(value=ast.Name(id='idx', ctx=ast.Load()))]
    module = ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[]))
    scope = dict(re=re, unicodedata=unicodedata, random=random, logger=logging.getLogger('test'), q_text='', _missing=[])
    exec(compile(module, filename, 'exec'), scope)
    return scope['select']


choose = extract('app.py')
old_choose = extract('app.before_answer_index_fix.txt')


class Tests(unittest.TestCase):
    def test_amount_is_answer_text_not_choice_number(self):
        answer = '3萬元以上75萬元以下。'
        options = [answer, '5萬元以上100萬元以下。', '3萬元以上150萬元以下。', '50萬元以上150萬元以下。']
        self.assertEqual(old_choose(answer, options), 2)
        self.assertEqual(choose(answer, options), 0)

    def test_deadline_inside_sentence_is_not_choice_number(self):
        answer = '自受理申訴之日起2個月內完成，必要時得延長 1個月。'
        options = [answer, '自組成調查小組之日起2個月內完成', '自第一次會議起2個月', '自第一次會議起1個月']
        self.assertEqual(old_choose(answer, options), 1)
        self.assertEqual(choose(answer, options), 0)

    def test_reversed_true_false_order(self):
        self.assertEqual(choose('正確', ['錯誤', '正確'], ['F', 'T']), 1)
        self.assertEqual(choose('不正確', ['錯誤', '正確'], ['F', 'T']), 0)
        self.assertEqual(choose('不正確', ['正確', '錯誤'], ['T', 'F']), 1)

    def test_plain_number_remains_supported(self):
        self.assertEqual(choose('2', ['甲', '乙', '丙', '丁']), 1)

    def test_labeled_number_remains_supported(self):
        self.assertEqual(choose('2. 乙', ['甲', '乙', '丙', '丁']), 1)

    def test_letter_remains_supported(self):
        self.assertEqual(choose('C', ['甲', '乙', '丙', '丁']), 2)

    def test_decimal_in_answer_not_a_numbered_label(self):
        self.assertEqual(choose('3.5萬元', ['甲', '3.5萬元', '丙', '丁']), 1)


if __name__ == '__main__':
    unittest.main()
