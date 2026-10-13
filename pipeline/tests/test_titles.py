import unittest

from pipeline.titles import collision_suffix, fix_typos, make_file_stem, slugify


class TestFixTypos(unittest.TestCase):
    def test_all_known_typos(self):
        cases = {
            "How to explain a project you workede on in 30 Minutes":
                "How to explain a project you worked on in 30 Minutes",
            "250 Essential Email Phrases & How to Wriet an email professionally":
                "250 Essential Email Phrases & How to Write an email professionally",
            "100 Negotiation Phrases for Busness Meetings & Practical Dialogues":
                "100 Negotiation Phrases for Business Meetings & Practical Dialogues",
            "How to talk About Salary Negotiation & 10 Essential Approchers":
                "How to talk About Salary Negotiation & 10 Essential Approaches",
            "How to Speakup in Business Meeting with Confidence & 100 Essential Phrases":
                "How to Speak up in Business Meeting with Confidence & 100 Essential Phrases",
            "How to Communicate confidentially in workplace":
                "How to Communicate confidently in workplace",
        }
        for raw, want in cases.items():
            fixed, applied = fix_typos(raw)
            self.assertEqual(fixed, want, msg=raw)
            self.assertTrue(applied, msg=raw)

    def test_no_false_positive(self):
        # 'speak' 整词 ≠ 'Speakup'；'worked' ≠ 'workede'
        fixed, applied = fix_typos("How to speak clearly about work you worked on")
        self.assertEqual(fixed, "How to speak clearly about work you worked on")
        self.assertEqual(applied, [])

    def test_case_preserved(self):
        self.assertEqual(fix_typos("wriet it down")[0], "write it down")
        self.assertEqual(fix_typos("WRIET IT DOWN")[0], "Write IT DOWN")

    def test_multiple_fixes_recorded(self):
        fixed, applied = fix_typos("Wriet a Busness email")
        self.assertEqual(fixed, "Write a Business email")
        self.assertEqual(sorted(applied), ["Busness→Business", "Wriet→Write"])


class TestSlugify(unittest.TestCase):
    def test_plan_example(self):
        self.assertEqual(slugify("How to explain a project you worked on"),
                         "Howtoexplainaprojectyouworkedon")

    def test_curly_quotes_stripped(self):
        self.assertEqual(
            slugify("\u201cCan you catch the 75 Essential Business Phrases\u201d REAL Business MEETING"),
            "Canyoucatchthe75EssentialBusinessPhrasesREALBusinessMEETING")

    def test_punctuation_removed(self):
        self.assertEqual(
            slugify("100 Negotiation Phrases for Busness Meetings & Practical Dialogues"),
            "100NegotiationPhrasesforBusnessMeetingsPracticalDialogues")
        self.assertEqual(
            slugify("How to Describe Your Job Tasks & Responsibilities Professionally. (Part 2)"),
            "HowtoDescribeYourJobTasksResponsibilitiesProfessionallyPart2")

    def test_hyphen_kept(self):
        self.assertEqual(
            slugify("Business English Conversation - English Dialogs at Work"),
            "BusinessEnglishConversation-EnglishDialogsatWork")

    def test_camelcase_preserved(self):
        self.assertEqual(slugify("How to Get Promoted at Work Faster"),
                         "HowtoGetPromotedatWorkFaster")

    def test_length_cap(self):
        slug = slugify("A" * 500)
        self.assertLessEqual(len(slug), 200)


class TestFileStem(unittest.TestCase):
    def test_zero_padding(self):
        self.assertEqual(make_file_stem(1, "Foo"), "01_Foo")
        self.assertEqual(make_file_stem(67, "Foo"), "67_Foo")

    def test_collision_suffix(self):
        self.assertEqual(collision_suffix("38192677309"), "_c677309")


if __name__ == "__main__":
    unittest.main()
