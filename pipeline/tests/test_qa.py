import unittest

from pipeline import transcribe_stage


def seg(start, end, text):
    return {"start": start, "end": end, "text": text}


class TestSparseSegments(unittest.TestCase):
    def test_normal_dense_segments_not_flagged(self):
        segments = [seg(0, 8, "We are happy with that timeline and we can deliver it.")]
        self.assertEqual(transcribe_stage.sparse_segments(segments, 10), [])

    def test_long_short_segment_flagged(self):
        # 11.3 秒只有 5 个词：WhisperX 吞掉整句的典型形态
        segments = [seg(327.4, 338.7, "That's always a good sign.")]
        found = transcribe_stage.sparse_segments(segments, 10)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["span"], 11.3)
        self.assertEqual(found[0]["words"], 5)

    def test_many_words_not_flagged(self):
        segments = [seg(0, 10, "one two three four five six seven eight")]
        self.assertEqual(transcribe_stage.sparse_segments(segments, 10), [])

    def test_tail_farewell_ignored(self):
        # 10 分钟片长，590s 在最后 30 秒内，不计
        segments = [seg(590, 600, "Goodbye.")]
        self.assertEqual(transcribe_stage.sparse_segments(segments, 10), [])

    def test_short_span_not_flagged(self):
        segments = [seg(0, 5, "Goodbye.")]
        self.assertEqual(transcribe_stage.sparse_segments(segments, 10), [])

    def test_qa_check_warning_is_non_blocking(self):
        segments = [seg(0, 30, "We are happy with that timeline and can deliver the project."),
                    seg(20, 31, "That's always a good sign."),
                    seg(40, 60, "We keep practicing every day and improve our English.")]
        qa = transcribe_stage.qa_check(segments, 1)
        self.assertEqual(qa["flags"], [])
        self.assertEqual(qa["warnings"], ["sparse"])
        self.assertEqual(qa["sparse_count"], 1)
        self.assertEqual(qa["sparse"][0]["words"], 5)


if __name__ == "__main__":
    unittest.main()
