"""阶段四（脚本侧）：候选词/短语提取 + 全局词表索引 + 00_* 渲染。

不用 spaCy：英文商务语料 + 下游 LLM 精选，候选只需召回率（评审确认）。
vocab_index 每次从全部 candidates.json 全量重建——幂等（评审修正 #4）。
00_总词表 / 00_INDEX 每次重新渲染，绝不追加。
"""
import json
import re
from collections import Counter, defaultdict

from . import config, state

WORD_RE = re.compile(r"[a-zA-Z']+(?:-[a-zA-Z']+)*")

STOPWORDS = frozenset("""
a about above after again against all am an and any are arent as at be because been
before being below between both but by can cant cannot could couldnt did didnt do does
doesnt doing dont down during each few for from further had hadnt has hasnt have havent
having he her here hers herself him himself his how i id if ill im in into is isnt it
its itself ive just lets me more most mustnt my myself no nor not of off on once only
or other ought our ours ourselves out over own same shant she should shouldnt so some
such than that thats the their theirs them themselves then there these they this those
through to too under until up very was wasnt we were werent what when where which while
who whom why with will wont would wouldnt shall may might must yes yeah yep nope
you your yours yourself yourselves
okay ok yeah yep um uh hmm gonna wanna gotta right now well like really actually basically
dont cant wont didnt isnt arent youre theyre whats ive id
""".split())

IRREGULAR = {
    "was": "be", "were": "be", "been": "be", "am": "be", "is": "be", "are": "be",
    "has": "have", "had": "have", "did": "do", "does": "do",
    "went": "go", "goes": "go", "gone": "go",
    "made": "make", "met": "meet", "said": "say", "got": "get", "gotten": "get",
    "took": "take", "taken": "take", "knew": "know", "known": "know",
    "thought": "think", "told": "tell", "found": "find",
    "gave": "give", "given": "give", "came": "come", "felt": "feel",
    "left": "leave", "kept": "keep", "ran": "run", "seen": "see", "saw": "see",
    "spoke": "speak", "spoken": "speak", "wrote": "write", "written": "write",
    "men": "man", "women": "woman", "people": "person", "children": "child",
    "better": "good", "best": "good", "worse": "bad", "worst": "bad",
}

# CEFR A1-A2 常见词表（Oxford 3000 子集的近似；用于分层，召回导向非权威）。
# 不在此表 → B1+。标题实词不受此表影响（硬性入选核心词，见 PLAN §5 v2）。
A1_A2_WORDS = frozenset("""
about across afraid after again against ago agree air all also always and angry animal
another answer any anyone anything apartment appear apple area arm around arrive art as
ask at away baby back bad bag ball bank basic bathroom be bear beat beautiful because
become bed bedroom before begin behind believe bell below beside best better between big
bike bird birthday bit black block blue board boat body book bored born both bottle box
boy bread breakfast bridge bright bring brother brown build building bus busy but buy by
call camera camp can car card care carry case cat catch cause cell central centre century
certain chair chance change cheap check cheese chicken child choose church cinema circle
city class classmate clean clear clever climb clock close clothes cloud club coast coffee
cold collect college colour come common computer cook cool copy corner correct cost could
count country course cousin cover create cry culture cup cut dance dangerous daughter day
dead decide deep delicious depend describe diary dictionary die different difficult dinner
dirty discover dish do doctor dog dollar door double draw dream dress drink drive driver
drop dry during each ear early earth east easy eat egg eight else end enemy english enjoy
enough enter eraser evening ever every everyone everything example excited exciting
exercise expect expensive experience eye face fact factory fail fair fall false family
famous farm fast fat father favourite fear feel female few field fifteen film find fine
finger finish fire first fish fit five flat floor flower fly follow food foot football for
foreign forest forget fork form forty forward four free fresh friend from front fruit full
fun future game garden gas gate general geography get gift girl give glass go god gold
good grade grandparent grass great green grey ground group grow guess guest guide gun hair
half hall hand hang happen happy hard hat hate have he head healthy hear heart heavy hello
help her here hero hers herself hi hide high him himself his history hit hobby hold hole
holiday home homework honest hope horse hospital hot hotel hour house how however hundred
hungry hurry hurt husband i ice idea if ill imagine important in inch include increase
inside instead interest into introduce invite iron island issue it its itself jacket job
join joke journey joy juice jump just keep key kick kill kind king kitchen knee knife
knock know lack lady lake land language large last late laugh law lay lazy lead learn
least leave left leg lend less lesson let letter library lie life lift light like line
lion list listen little live local lonely long look lose lot loud love low luck lunch
machine magazine mail main make man many map mark market marry match material math matter
may maybe meal mean meat medicine meet member memory mention mess middle might mile milk
mind mine minute miss mistake modern moment money monkey month moon more morning most
mother mountain mouse mouth move movie mr mrs much music must my myself name national
natural near nearly neck need neighbor nephew never new news next nice niece night nine
nobody noise none nor normal north nose not note nothing notice now nurse ocean of off
offer office officer often oil old on once one online only open opposite or orange order
other our ours ourselves out outside over own page paint pair palace paper parent park
part party pass past pay pen pencil people pepper perfect perhaps period person pet phone
photo piano pick picnic picture piece pig pilot pink pizza place plan plane plant plate
play please plus pocket point police polite pool poor popular possible post potato pound
practice prepare present pretty prevent price prince princess private prize probably
problem produce program promise protect proud provide public pull purple purpose push put
quality quarter queen question quick quiet quite race radio railway rain raise rather
reach read ready real reason receive record red reflect refuse regular relax remain
remember remind remove rent repair repeat reply report rest restaurant result return rich
ride right ring rise risk river road rock roof room round row rub rubbish rule run sad
safe sale salt same sand save say scene school science scientist score sea search season
seat second secret see seem sell send sense sentence separate serious serve seven several
shade shake shall shape share she sheep shine ship shirt shoe shop short should shoulder
shout show sick side sign silver similar simple since sing singer single sink sister sit
site situation six size ski skill skirt sky sleep slide slow small smell smile smoke snow
so soap soccer social sock soft soldier solve some someone something sometimes son song
soon sorry sound soup south space speak special speed spell spend sport spot spread spring
square stage stairs stand star start state station stay steak step still stone stop store
storm story straight strange street strong student study stupid subject succeed such
sudden sugar suggest suit summer sun supermarket support suppose sure surf surprise sweet
swim system table tail take talk tall taste taxi tea teach teacher team tear telephone
television tell ten term terrible test text than thank that theater their them themselves
then there these they thick thin thing think third thirty this those though thought
thousand three throat through throw ticket tidy tie time tired to today together toilet
tomorrow ton tongue tonight too tool tooth top total touch tour tourist toward towel tower
town toy trade traffic train travel treat tree trip trouble truck true try turn tv twenty
twice two twin type ugly uncle under understand unfair unit university until up upon upper
upset us use useful usual vacation vegetable very victim video view village visit voice
wait wake walk wall want war warm wash waste watch water wave way we weak wear weather
wedding week weekend weight welcome well west wet what wheel when where whether which
while white who whole whom whose why wide wife will win wind window wine wing winter wish
with within without wolf woman wonder wood word work worker world worry worse worth would
write wrong yard year yellow yes yesterday yet you young your yours yourself youth zero zoo
""".split()) | frozenset("email formal informal grammar spelling".split())


def cefr_level(lemma):
    """A1-A2 熟词 → 'A2'；其余 → 'B1+'（召回导向，权威分级由 LLM 精选时复核）。"""
    return "A2" if lemma in A1_A2_WORDS else "B1+"


def title_keywords(title_fixed):
    """解析标题取实词：返回 [(display, lemma), ...]，供核心词硬性入选。"""
    out, seen = [], set()
    for tok in WORD_RE.findall(title_fixed):
        lem = normalize(tok)
        if not lem or lem in STOPWORDS or lem in seen or len(lem) < 2:
            continue
        seen.add(lem)
        out.append([tok.lower(), lem])
    return out


def normalize(word):
    """词形归一：不规则映射 + 后缀剥离（召回导向，一致性优先于精确性）。"""
    w = word.lower().replace("'", "")
    if not w:
        return ""
    if w in IRREGULAR:
        return IRREGULAR[w]
    if w in STOPWORDS:
        return w
    for suf in ("ing", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            if suf == "s" and w.endswith(("ss", "us", "is")):
                continue  # business/status/analysis 类非复数
            base = w[: -len(suf)]
            # stopped→stop 类双写辅音还原
            if (suf in ("ing", "ed") and len(base) >= 3
                    and base[-1] == base[-2] and base[-1] in "bdgmnprt"):
                base = base[:-1]
            return base
    return w


def build_candidates(ep, segments):
    sents = [{"t": s["start"], "text": s["text"]}
             for s in segments if s["text"].strip()]
    token_lists = [WORD_RE.findall(x["text"]) for x in sents]

    lemma_count = Counter()
    surfaces = defaultdict(Counter)
    for tokens in token_lists:
        for tok in tokens:
            lem = normalize(tok)
            if not lem or lem in STOPWORDS or len(lem) < 2:
                continue
            lemma_count[lem] += 1
            surfaces[lem][tok.lower()] += 1

    unigram_freq = [[surfaces[lem].most_common(1)[0][0], c, cefr_level(lem)]
                    for lem, c in sorted(lemma_count.items(),
                                         key=lambda kv: (-kv[1], kv[0]))[:120]]

    # 短语：bigram/trigram，首尾须为实词（中间允许停用词，如 "make a decision"），
    # count≥2，带首现例句与时间戳
    phrase_count = Counter()
    phrase_example = {}
    for tokens, sent in zip(token_lists, sents):
        lowers = [t.lower() for t in tokens]
        for size in (2, 3):
            for i in range(len(tokens) - size + 1):
                gram = lowers[i:i + size]
                if gram[0] in STOPWORDS or gram[-1] in STOPWORDS:
                    continue
                key = " ".join(gram)
                phrase_count[key] += 1
                if key not in phrase_example:
                    phrase_example[key] = {"text": key, "count": 0,
                                           "example": sent["text"], "t": sent["t"]}
    phrases = []
    for key, cnt in phrase_count.most_common():
        if cnt < 2 or len(phrases) >= 80:
            break
        d = phrase_example[key]
        d["count"] = cnt
        phrases.append(d)

    total_words = sum(len(t) for t in token_lists)
    return {
        "file_stem": ep["file_stem"],
        "no": ep["no"],
        "title_keywords": title_keywords(ep.get("title_fixed", "")),
        "unigram_freq": unigram_freq,
        "chunks": phrases[:10],  # 语块候选，LLM 精选 3~5 条（PLAN §5 v2）
        "phrases": phrases,
        "sentences": sents,
        "lemmas": [[lem, c] for lem, c in lemma_count.most_common()],
        "stats": {"total_words": total_words,
                  "unique_words": len(lemma_count),
                  "duration_min": ep["duration_min"]},
    }


def extract_and_write(ep, segments):
    payload = build_candidates(ep, segments)
    outdir = config.CANDIDATES_DIR / ep["file_stem"]
    state.write_text_atomic(outdir / "candidates.json",
                            json.dumps(payload, ensure_ascii=False, indent=2))
    return outdir


def rebuild_vocab_index():
    """从全部 candidates.json 全量重建 vocab_index.json（幂等）。"""
    index = {}
    for cdir in sorted(config.CANDIDATES_DIR.glob("*/candidates.json")):
        data = json.loads(cdir.read_text(encoding="utf-8"))
        no = data["no"]
        for lem, cnt in data.get("lemmas", []):
            e = index.setdefault(lem, {"display": lem, "count": 0, "episodes": [],
                                       "level": cefr_level(lem)})
            e["count"] += cnt
            if no not in e["episodes"]:
                e["episodes"].append(no)
    state.write_text_atomic(config.VOCAB_INDEX_JSON,
                            json.dumps(index, ensure_ascii=False, indent=2))
    return index


def render_vocab_md(index):
    lines = ["# 00 · 总词表", "",
             "> 字母序；「集」列为该词作为候选出现的集号（1–67）；",
             "CEFR 为程序近似分级（A2=常见熟词，B1+=核心词备选）。",
             "各集最终收录以该集 md 为准。", "",
             "| 词 | 出现集号 | CEFR |", "|---|---|---|"]
    for lem in sorted(index):
        e = index[lem]
        eps = "、".join(str(n) for n in sorted(e["episodes"]))
        lines.append(f"| {e['display']} | {eps} | {e.get('level', 'B1+')} |")
    return "\n".join(lines) + "\n"


def render_index_md(episodes):
    lines = ["# 00_INDEX · 目录", "",
             "| 编号 | 标题 | 时长(分) | 状态 | 候选词数 | 文件 |",
             "|---:|---|---:|---|---:|---|"]
    for ep in episodes:
        words = ""
        cand = config.CANDIDATES_DIR / ep["file_stem"] / "candidates.json"
        if cand.exists():
            stats = json.loads(cand.read_text(encoding="utf-8"))["stats"]
            words = str(stats["unique_words"])
        md_name = f"{ep['file_stem']}.md"
        link = (f"[{md_name}]({md_name})"
                if (config.OUTPUT_DIR / md_name).exists() else md_name)
        lines.append(f"| {ep['no']} | {ep['title_fixed']} | {ep['duration_min']} "
                     f"| {ep['status']} | {words} | {link} |")
    return "\n".join(lines) + "\n"


def render_indexes():
    """渲染 00_总词表.md 与 00_INDEX.md 到 OUTPUT_DIR（始终全量重渲染）。"""
    episodes = state.load()
    index = {}
    if config.VOCAB_INDEX_JSON.exists():
        index = json.loads(config.VOCAB_INDEX_JSON.read_text(encoding="utf-8"))
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    state.write_text_atomic(config.OUTPUT_DIR / "00_总词表.md",
                            render_vocab_md(index))
    state.write_text_atomic(config.OUTPUT_DIR / "00_INDEX.md",
                            render_index_md(episodes))
    print(f"已渲染 {config.OUTPUT_DIR / '00_总词表.md'}")
    print(f"已渲染 {config.OUTPUT_DIR / '00_INDEX.md'}")
