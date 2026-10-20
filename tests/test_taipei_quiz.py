"""Taipei quiz regressions: no browser, real exams, network, or paid AI calls."""
import ast
import contextlib
import difflib
import io
import json
from pathlib import Path
import re
import types
import unittest
from unittest.mock import Mock


SOURCE = Path(__file__).resolve().parents[1] / 'quiz_bank.py'


def module_functions():
    ns = {'re': re, 'json': json, 'difflib': difflib, 'time': types.SimpleNamespace(sleep=Mock())}
    tree = ast.parse(SOURCE.read_text(encoding='utf8'))
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(SOURCE), 'exec'), ns)
    return ns


def entry(texts, val):
    return dict(val=str(val), **{'opt' + str(i): text for i, text in enumerate(texts)})


class TaipeiQuizTests(unittest.TestCase):
    def setUp(self):
        self.ns = module_functions()

    def test_bank_maps_text_after_options_shuffle(self):
        result = self.ns['lookup_bank']({'問題': entry(['a. 甲', 'b. 乙'], 0)},
                                        '問題', options={'0': 'a. 乙', '1': 'b. 甲'})
        self.assertEqual(result, '1')

    def test_changed_or_ambiguous_options_never_reuse_radio_number(self):
        lookup = self.ns['lookup_bank']
        self.assertIsNone(lookup({'問題': entry(['甲', '乙'], 0)}, '問題', options={'0': '甲', '1': '丙'}))
        self.assertIsNone(lookup({'問題': '0'}, '問題', options={'0': '甲', '1': '乙'}))
        self.assertIsNone(lookup({'問題': entry(['甲', '甲'], 0)}, '問題', options={'0': '甲', '1': '甲'}))

    def test_decimal_signs_and_percentages_keep_their_meaning(self):
        normalize = self.ns['_option_text']
        self.assertNotEqual(normalize('1.5%'), normalize('15%'))
        self.assertNotEqual(normalize('-5'), normalize('5'))
        self.assertNotEqual(normalize('50%'), normalize('50'))
        self.assertEqual(normalize('1. 1.5%'), normalize('1.5%'))

    def review(self, **kw):
        record = dict(options={'0': '甲', '1': '乙', '2': '丙'}, selected='0')
        record.update(kw)
        return self.ns['_review_result'](record)

    def test_checked_without_feedback_never_becomes_correct(self):
        self.assertEqual(self.review(), {'correct_val': None, 'wrong_val': None})
        self.assertIsNone(self.review(state='完成')['correct_val'])

    def test_incorrect_in_child_state_is_not_mistaken_for_correct(self):
        self.assertEqual(self.review(classes='que multichoice', state='不正確'),
                         {'correct_val': None, 'wrong_val': '0'})

    def test_wrong_binary_answer_is_inverted_only_with_explicit_feedback(self):
        options = {'0': '錯誤', '1': '正確'}
        self.assertEqual(self.review(options=options, selected='1', state='不正確')['correct_val'], '0')
        self.assertIsNone(self.review(options=options, selected='1')['correct_val'])

    def test_disclosed_answer_and_wrong_option_are_kept_separate(self):
        self.assertEqual(self.review(correct_values=['2'], state='不正確'),
                         {'correct_val': '2', 'wrong_val': '0'})
        self.assertEqual(self.review(rightanswer='正確答案為：乙')['correct_val'], '1')
        self.assertIsNone(self.review(correct_values=['0'], incorrect_values=['0'])['correct_val'])

    def setup_flow(self, questions, feedback, scores, bank, ai=None, config=None):
        self.ns.update({
            'gas_fetch_bank': Mock(return_value=bank),
            '_start_or_resume_quiz': Mock(return_value='attempt'),
            '_read_questions': Mock(side_effect=questions),
            '_read_review_feedback': Mock(side_effect=feedback),
            '_get_score_from_review': Mock(side_effect=scores),
            '_fill_answers': Mock(), '_submit_quiz': Mock(),
            'gas_save_questions': Mock(return_value=True),
            'gas_report_missing_questions': Mock(),
            'ai_guess_answer': Mock(side_effect=ai),
        })
        with contextlib.redirect_stdout(io.StringIO()):
            result = self.ns['do_quiz_with_bank'](Mock(), Mock(), '5609', 'quiz',
                config=config or {}, course_name='測試課程')
        return result

    def test_stale_bank_cannot_override_wrong_feedback_or_shuffle(self):
        rounds = []
        for i in range(3):
            rounds.append([
                {'name': f'good{i}', 'qtext': '已確認題', 'options':
                    {'0': '正解', '1': '錯解'} if i == 0 else {'0': '錯解', '1': '正解'}},
                {'name': f'bad{i}', 'qtext': '原錯題', 'options':
                    {'0': '甲', '1': '乙', '2': '丙', '3': '丁'} if i < 2
                    else {'0': '丁', '1': '乙', '2': '甲', '3': '丙'}},
            ])
        feedback = [
            {'good0': {'correct_val': '0', 'wrong_val': None}, 'bad0': {'correct_val': None, 'wrong_val': '0'}},
            {'good1': {'correct_val': '1', 'wrong_val': None}, 'bad1': {'correct_val': None, 'wrong_val': '1'}},
            {'good2': {'correct_val': '1', 'wrong_val': None}, 'bad2': {'correct_val': '3', 'wrong_val': None}},
        ]
        targets = iter(['乙', '丙'])
        ai_options = []
        def ai(qtext, options, config):
            ai_options.append(options)
            target = next(targets)
            return next(v for v, text in options.items() if text == target)
        bank = {'已確認題': entry(['正解', '錯解'], 0), '原錯題': entry(['甲', '乙', '丙', '丁'], 0)}
        result = self.setup_flow(rounds, feedback, ['得50分', '得50分', '得100分'], bank,
                                 ai, {'ai_api_key': 'test-only'})
        self.assertEqual(result, ('得100分', True))
        self.assertEqual(len(ai_options), 2)
        self.assertNotIn('甲', ai_options[0].values())
        self.assertNotIn('甲', ai_options[1].values())
        self.assertNotIn('乙', ai_options[1].values())
        selections = [c.args[1] for c in self.ns['_fill_answers'].call_args_list]
        self.assertEqual(selections, [{'good0':'0', 'bad0':'0'}, {'good1':'1', 'bad1':'1'}, {'good2':'1', 'bad2':'3'}])
        saved = [q for c in self.ns['gas_save_questions'].call_args_list for q in c.args[1]]
        self.assertEqual([(q['q_text'], q['opt' + q['val']]) for q in saved], [('已確認題', '正解'), ('原錯題', '丙')])
        self.assertTrue(all(q['evidence'] == 'platform_feedback' for q in saved))

    def test_unknown_feedback_rechecks_bank_but_does_not_exclude_unproven_answer(self):
        q = {'name': 'q', 'qtext': '問題', 'options': {'0': '甲', '1': '乙', '2': '丙'}}
        result = self.setup_flow([[q]] * 3, [{}] * 3, ['得50分'] * 3,
                                {'問題': entry(['甲', '乙', '丙'], 0)},
                                lambda *args: '1', {'ai_api_key': 'test-only'})
        self.assertEqual(result, ('得50分', False))
        self.assertEqual(self.ns['ai_guess_answer'].call_count, 2)
        self.assertEqual(self.ns['ai_guess_answer'].call_args.args[1], q['options'])
        self.ns['gas_save_questions'].assert_not_called()
        self.assertEqual(self.ns['_submit_quiz'].call_count, 3)

    def test_no_ai_uses_feedback_without_repeating_rejected_options(self):
        q = {'name': 'q', 'qtext': '問題', 'options': {'0': '甲', '1': '乙', '2': '丙', '3': '丁'}}
        feedback = [{'q': {'correct_val': None, 'wrong_val': str(i)}} for i in range(3)]
        self.setup_flow([[q]] * 3, feedback, ['得50分'] * 3, {'問題': entry(['甲', '乙', '丙', '丁'], 0)})
        self.assertEqual([c.args[1]['q'] for c in self.ns['_fill_answers'].call_args_list], ['0', '1', '2'])
        self.ns['ai_guess_answer'].assert_not_called()
        self.ns['gas_save_questions'].assert_not_called()

    def test_total_score_alone_does_not_publish_guessed_answer(self):
        q = {'name': 'q', 'qtext': '問題', 'options': {'0': '甲', '1': '乙'}}
        result = self.setup_flow([[q]], [{}], ['得100分'], {})
        self.assertEqual(result, ('得100分', True))
        self.ns['gas_save_questions'].assert_not_called()

    def test_multiselect_bank_maps_complete_set_after_shuffle(self):
        stored = dict(entry(['甲', '乙', '丙', '丁'], '["0", "2"]'), type='multiselect')
        lookup = self.ns['lookup_bank']
        self.assertEqual(lookup({'問題': stored}, '問題', options={'0':'丙','1':'甲','2':'丁','3':'乙'}, multiple=True), ['0','1'])
        stored['val'] = '["0", "0"]'
        self.assertIsNone(lookup({'問題': stored}, '問題', options={'0':'甲','1':'乙','2':'丙','3':'丁'}, multiple=True))

    def test_partial_multiselect_rejects_combination_without_inventing_correct_set(self):
        result = self.review(multiple=True, selected_values=['0','1'], state='部分正確', correct_values=['0'])
        self.assertEqual(result, {'correct_val':None, 'wrong_val':None, 'wrong_combination':['0','1']})
        self.assertEqual(self.review(multiple=True, selected_values=['0','2'], state='正確')['correct_val'], ['0','2'])
        self.assertIsNone(self.review(multiple=True, selected_values=['0'], state='正確', incorrect_values=['0'])['correct_val'])

    def test_multiselect_disclosed_answer_requires_unambiguous_complete_text(self):
        parse = self.ns['_disclosed_multiple']
        self.assertEqual(parse('正確答案為：甲，乙, 丙', {'0':'甲，乙','1':'丙','2':'丁'}), ['0','1'])
        self.assertIsNone(parse('正確答案為：甲，乙', {'0':'甲','1':'乙','2':'甲，乙'}))
        self.assertIsNone(parse('甲, 丙', {'0':'甲','1':'丙'}))
        result = self.review(multiple=True, selected_values=['0'], state='部分正確', rightanswer='正確答案是：甲, 丙')
        self.assertEqual(result['correct_val'], ['0','2'])
        self.assertEqual(result['wrong_combination'], ['0'])

    def test_checkbox_fill_uses_unique_names_and_clears_old_choices(self):
        self.ns['By'] = types.SimpleNamespace(CSS_SELECTOR='css')
        boxes = {str(i):Mock() for i in range(3)}
        selected = {'0':True,'1':True,'2':False}
        for i, box in boxes.items():
            box.is_selected.side_effect = lambda i=i: selected[i]
        driver = Mock()
        def find(by, selector):
            self.assertEqual(by, 'css')
            self.assertIn('input[type=checkbox]', selector)
            return boxes[re.search(r'name="q(\d)"', selector)[1]]
        def click(script, box):
            i = next(i for i,b in boxes.items() if b is box)
            selected[i] = not selected[i]
        driver.find_element.side_effect = find
        driver.execute_script.side_effect = click
        q = dict(name='q0', type='multiselect', options={'0':'甲','1':'乙','2':'丙'}, input_names={str(i):'q'+str(i) for i in range(3)})
        self.ns['_fill_answers'](driver, {'q0':['0','2']}, [q])
        self.assertEqual([c.args[1] for c in driver.execute_script.call_args_list], [boxes['1'], boxes['2']])
        self.assertEqual(selected, {'0':True,'1':False,'2':True})
        driver.execute_script.side_effect = None
        with self.assertRaises(ValueError):
            self.ns['_fill_answers'](driver, {'q0':['1']}, [q])

    def test_unsupported_question_blocks_stop_before_submission(self):
        driver = Mock()
        driver.execute_script.return_value = {'questions':[], 'coverage':[{'type':'essay'}]}
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(ValueError):
            self.ns['_read_questions'](driver)

    def test_multiselect_retry_keeps_individually_correct_options_and_saves_once(self):
        q = dict(name='multi', qtext='複選問題', type='multiselect', options={'0':'甲','1':'乙','2':'丙'})
        feedback = [
            {'multi':{'correct_val':None, 'wrong_val':None, 'wrong_combination':['0']}},
            {'multi':{'correct_val':['0','2'], 'wrong_val':None, 'wrong_combination':None}}]
        def ai(text, options, config, **kw):
            self.assertIn('甲', options.values())
            self.assertEqual(kw['rejected_combinations'], [['甲']])
            return ['0','2']
        result = self.setup_flow([[q],[q]], feedback, ['得60分','得100分'], {}, ai, {'ai_api_key':'test-only'})
        self.assertEqual(result, ('得100分', True))
        saved = self.ns['gas_save_questions'].call_args.args[1]
        self.assertEqual(len(saved), 1)
        self.assertEqual(json.loads(saved[0]['val']), ['0','2'])
        self.assertEqual(saved[0]['evidence'], 'platform_feedback')


if __name__ == '__main__':
    unittest.main()
