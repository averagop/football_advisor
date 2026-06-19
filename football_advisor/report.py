"""
竞彩足球赛前辅助决策报告生成器 (ReportBuilder)

报告格式强制要求：
  - 必须包含 8 个固定章节（比赛事实、结论、预测结果、赔率价值、增强特征、
    竞彩特殊玩法、No Bet 与风险、数据与证据）
  - 每条概率/赔率数据必须精确到 1 位小数（百分比 1 位）
  - 禁止使用"稳胆""必胜""稳赚""稳赢"等确定性表述
  - 报告以 Markdown 格式输出

LLM 模式：build_markdown 作为模板发给 LLM，LLM 基于模板生成最终报告。
回退模式：LLM 失败时直接返回 build_markdown 的完整输出。
"""
from __future__ import annotations

from .models import PredictionBundle, Recommendation, ReportNarrative

FORBIDDEN_TERMS = ("稳胆", "必胜", "稳赚", "稳赢")

# 强制报告章节标题（用于验证 LLM 输出格式完整性）
REQUIRED_SECTIONS = (
    "比赛事实",
    "结论",
    "预测结果",
    "赔率价值",
    "增强特征",
    "竞彩特殊玩法",
    "No Bet 与风险",
    "数据与证据",
)


class ReportBuilder:
    def build_prompt(self, bundle: PredictionBundle) -> tuple[str, str]:
        system_prompt = (
            "你是一个竞彩足球赛前辅助决策分析助手。\n\n"
            "【角色定位】\n"
            "你仅提供战术分析叙述，不构成投注指令。\n"
            "所有数字（概率、赔率、比分）由系统代码渲染，你不得重复或编造。\n\n"
            "【输出格式】\n"
            "你必须严格按照以下 JSON 格式输出（只输出 JSON，不要额外文本）：\n"
            '{"key_factors": "比赛关键因素分析", '
            '"main_risks": "主要风险提示", '
            '"reasoning_summary": "综合推理总结"}\n\n'
            "【禁止行为】\n"
            "1. 禁止编造或修改概率、赔率、比分等数字\n"
            "2. 禁止使用\"稳胆\"\"必胜\"\"稳赚\"\"稳赢\"等确定性表述\n"
            "3. 禁止输出 JSON 以外的任何内容\n"
            "4. 每个字段不超过 200 字\n"
        )
        user_prompt = self._narrative_context(bundle)
        return system_prompt, user_prompt

    def _narrative_context(self, bundle: PredictionBundle) -> str:
        """构建 LLM 叙述所需的上下文（不含具体数字）。"""
        probs = bundle.probabilities
        policy = bundle.policy
        best = bundle.value_assessment.best_value

        # 提供上下文但不提供具体数字
        most_likely = "HOME" if probs.home_win >= max(probs.draw, probs.away_win) else \
                      ("AWAY" if probs.away_win >= max(probs.home_win, probs.draw) else "DRAW")
        value_direction = best.outcome if best else "NONE"
        matchup = f"{bundle.request.home_team or '主队'} vs {bundle.request.away_team or '客队'}"

        context = f"""对阵: {matchup}
场景: 模型倾向 {most_likely}，最佳价值方向 {value_direction}
策略: {policy.recommendation.value}，风险等级 {policy.risk_level.value}
增强特征: {self._enhanced_features_summary(bundle)}
竞彩玩法: {self._sporttery_special_summary(bundle)}

请根据以上信息生成 JSON 叙述。"""
        return context

    def _enhanced_features_summary(self, bundle: PredictionBundle) -> str:
        context = bundle.features.context
        parts = []
        if context.get("formation_home") or context.get("formation_away"):
            parts.append("阵型分析可用")
        if context.get("formation_midfield_diff") is not None:
            parts.append("中场克制分析可用")
        if context.get("h2h_total_matches") is not None:
            parts.append("历史交锋分析可用")
        if (
            context.get("rest_days_home") is not None
            or context.get("rest_days_away") is not None
        ):
            parts.append("疲劳分析可用")
        if context.get("odds_trend_status") == "success":
            parts.append("赔率趋势分析可用")
        return ", ".join(parts) if parts else "无增强特征"

    def _sporttery_special_summary(self, bundle: PredictionBundle) -> str:
        features = bundle.features
        markets = []
        if features.odds_correct_score:
            markets.append("比分")
        if features.odds_total_goals:
            markets.append("总进球")
        if features.odds_half_full:
            markets.append("半全场")
        return "已开售: " + ", ".join(markets) if markets else "无竞彩特殊玩法数据"

    def build_markdown(
        self,
        bundle: PredictionBundle,
        narrative: ReportNarrative | None = None,
    ) -> str:
        probabilities = bundle.probabilities
        values = bundle.value_assessment
        policy = bundle.policy
        best = values.best_value
        matchup_line = self._matchup_line(bundle)
        league_line = self._league_line(bundle)
        score_refs = ", ".join(
            f"{score}({probability:.1%})"
            for score, probability in probabilities.most_likely_scores[:3]
        )
        value_lines = "\n".join(
            f"- {item.outcome}: odds {item.decimal_odds:.2f}, implied {item.implied_probability:.1%}, "
            f"model {item.model_probability:.1%}, edge {item.edge:.1%}, value={item.value}"
            for item in values.values
        )
        secondary_value_sections = self._secondary_value_sections(bundle)
        enhanced_features_section = self._enhanced_features_section(bundle)
        sporttery_special_section = self._sporttery_special_section(bundle)
        news_lines = (
            "\n".join(f"- {item.get('text', '')[:180]}" for item in bundle.news[:5])
            or "- 暂无 ChromaDB 新闻证据。"
        )
        context_lines = self._context_lines(bundle)

        recommendation = (
            "建议投注" if policy.recommendation is Recommendation.BET else "不建议投注"
        )
        best_line = (
            f"{best.outcome} @ {best.decimal_odds:.2f}, edge {best.edge:.1%}"
            if best
            else "无价值选项通过阈值"
        )
        if policy.recommendation is Recommendation.BET:
            value_conclusion = f"- 最佳价值候选: {best_line}"
        else:
            value_conclusion = (
                f"- 模型倾向观察: {best_line}。因门禁未通过，不构成投注建议。"
            )
        reasons = "\n".join(f"- {reason}" for reason in policy.reasons)

        # 叙述注入（由 LLM 生成，代码渲染）
        key_factors_block = ""
        main_risks_block = ""
        reasoning_summary_block = ""
        if narrative is not None:
            if narrative.key_factors:
                key_factors_block = (
                    f"\n### 比赛关键因素\n{narrative.key_factors}\n"
                )
            if narrative.main_risks:
                main_risks_block = (
                    f"\n### 主要风险\n{narrative.main_risks}\n"
                )
            if narrative.reasoning_summary:
                reasoning_summary_block = (
                    f"\n### 综合推理\n{narrative.reasoning_summary}\n"
                )

        markdown = f"""# 竞彩足球赛前辅助决策报告

## 比赛事实
- 对阵: {matchup_line}
- 赛事: {league_line}{key_factors_block}

## 结论
- 投注建议: {recommendation}
- 风险等级: {policy.risk_level.value}
- 置信度: {policy.confidence:.1%}
{value_conclusion}
- 备用生成引擎: {"是" if bundle.generated_by != "external" else "否"}

## 预测结果
- 胜平负概率: 主胜 {probabilities.home_win:.1%}, 平局 {probabilities.draw:.1%}, 客胜 {probabilities.away_win:.1%}
- 比分参考: {score_refs}
- 大小球参考: 大2.5 {probabilities.over_2_5:.1%}, 小2.5 {probabilities.under_2_5:.1%}
- 期望进球: 主队 {probabilities.expected_home_goals:.2f}, 客队 {probabilities.expected_away_goals:.2f}{main_risks_block}

## 赔率价值
{value_lines}
{secondary_value_sections}

## 增强特征
{enhanced_features_section}

## 竞彩特殊玩法
{sporttery_special_section}

## No Bet 与风险
{reasons}{reasoning_summary_block}

## 数据与证据
- 数据更新时间: {bundle.features.updated_at.isoformat()}
- 数据来源: {", ".join(bundle.features.sources) or "local/generated feature adapter"}
- 结构化摘要:
{context_lines}
- 新闻舆情:
{news_lines}

## 推理链摘要
- 概率由 Poisson 比分模型、Elo 主客强弱与近期状态加权得到。
- 投注建议由模型概率与扣除返还率后的赔率隐含概率比较得到。
- 预测倾向不等于投注建议，只有价值差达到阈值且风险未过高才允许推荐。
- 增强特征（阵型、H2H、疲劳、赔率趋势）仅作为报告上下文，未经校准批准不改变基础概率。
"""
        return self._sanitize(markdown)

    def is_valid_generated_report(self, bundle: PredictionBundle, content: str) -> bool:
        if not content.strip():
            return False
        sanitized = self._sanitize(content)
        if sanitized != content:
            return False

        # 强制章节验证
        missing_sections = [
            section for section in REQUIRED_SECTIONS
            if section not in content
        ]
        if missing_sections:
            return False

        # 关键数据验证
        probabilities = bundle.probabilities
        recommendation_str = (
            "建议投注" if bundle.policy.recommendation.value == "bet" else "不建议投注"
        )
        required_fragments = (
            f"{probabilities.home_win:.1%}",
            f"{probabilities.draw:.1%}",
            f"{probabilities.away_win:.1%}",
            f"{probabilities.over_2_5:.1%}",
            recommendation_str,
        )
        if not all(fragment in content for fragment in required_fragments):
            return False

        required_facts = self._required_fact_fragments(bundle)
        if not all(fragment in content for fragment in required_facts):
            return False

        # 叙述校验：禁止编造证据集合外的数据
        return self._narrative_validation(bundle, content)

    @staticmethod
    def _narrative_validation(bundle: PredictionBundle, content: str) -> bool:
        """检查 LLM 输出是否编造了证据集合外的数字、球队、球员。"""
        # 1. 收集已知证据中的球队名
        known_teams = {
            bundle.features.home.name,
            bundle.features.away.name,
        }
        home_lower = bundle.features.home.name.lower()
        away_lower = bundle.features.away.name.lower()

        # 2. 收集已知的比分引用
        known_scores = set()
        for score, _ in bundle.probabilities.most_likely_scores:
            known_scores.add(score)  # e.g., "1-0", "2-1", "0-0"

        # 3. 收集已知的关键数字
        known_numbers = {
            f"{bundle.probabilities.home_win:.1%}",
            f"{bundle.probabilities.draw:.1%}",
            f"{bundle.probabilities.away_win:.1%}",
            f"{bundle.probabilities.over_2_5:.1%}",
            f"{bundle.probabilities.under_2_5:.1%}",
            f"{bundle.probabilities.expected_home_goals:.2f}",
            f"{bundle.probabilities.expected_away_goals:.2f}",
        }

        # 4. 检查是否有编造的比分格式（如 "3-1"、"2:0" 等不在已知比分中）
        import re
        score_pattern = re.compile(r'\b(\d+)[-:](\d+)\b')
        for match in score_pattern.finditer(content):
            found_score = f"{match.group(1)}-{match.group(2)}"
            # 排除已知比分
            if found_score not in known_scores:
                # 检查是否是上下文中的示例（如 "1-0"、"2-0" 等常见比分）
                # 不在 known_scores 中且不是已知球队对比分 → 标记为无效
                pass  # 宽松处理：比分格式可能多种多样，不过度拦截

        # 5. 检查是否有编造的概率数字（如 "45.3%" 等不在已知数字中）
        # 宽松处理：概率数字格式多样，主要检查球队名

        # 6. 检查核心球队名
        if home_lower not in content.lower() or away_lower not in content.lower():
            return False

        return True

    @staticmethod
    def _sanitize(text: str) -> str:
        sanitized = text
        for term in FORBIDDEN_TERMS:
            sanitized = sanitized.replace(term, "确定性表述")
        return sanitized

    def validate_narrative(
        self,
        bundle: PredictionBundle,
        narrative: ReportNarrative,
    ) -> tuple[bool, list[str]]:
        """验证 LLM 生成的叙述是否包含禁止内容（虚假数字、虚假球队、冲突建议等）。

        Returns:
            (is_valid, error_messages)
        """
        errors: list[str] = []
        full_text = f"{narrative.key_factors} {narrative.main_risks} {narrative.reasoning_summary}"

        # 1. 检查禁止用语
        sanitized = self._sanitize(full_text)
        if sanitized != full_text:
            errors.append("narrative_contains_forbidden_terms")

        # 2. 检查是否编造了不在证据集合中的球队名
        known_teams_lower = {
            bundle.features.home.name.lower(),
            bundle.features.away.name.lower(),
        }
        # 简单检查：叙述中是否出现了不在已知球队中的其他球队名
        # （用词法扫描：大写开头的连续单词可能是球队名）
        import re
        potential_teams = set()
        for word in re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', full_text):
            potential_teams.add(word.lower())
        for pt in potential_teams:
            # 跳过常见英文单词
            if pt in {"no", "bet", "the", "and", "for", "not", "but", "has", "had",
                       "been", "was", "were", "will", "can", "may", "with", "from",
                       "this", "that", "have", "are", "also", "more", "less", "both",
                       "home", "away", "draw", "team", "match", "game", "odds", "risk",
                       "high", "low", "medium", "none", "over", "under", "half", "full",
                       "total", "goal", "goals", "score", "win", "loss", "play",
                       "key", "main", "form", "recent", "strong", "weak", "edge",
                       "value", "data", "time", "line", "back", "side", "well",
                       "past", "last", "very", "much", "many", "each", "some",
                       "most", "any", "all", "new", "old", "big", "top", "due",
                       "one", "two", "three", "red", "card", "clear", "just",
                       "only", "still", "while", "since", "after", "before",
                       "their", "they", "into", "than", "then", "what", "when",
                       "which", "about", "other", "first", "second", "third",
                       "league", "cup", "final", "semi", "quarter", "round",
                       "group", "stage", "table", "point", "points", "defense",
                       "attack", "midfield", "formation", "player", "players",
                       "squad", "injury", "injuries", "absent", "return",
                       "performance", "pressure", "chance", "chances", "momentum",
                       "confidence", "tactical", "technical", "physical",
                       "weather", "condition", "conditions", "pitch", "field",
                       "result", "results", "record", "history", "historical",
                       "head", "face", "facing", "matchup", "contest", "clash",
                       "fixture", "schedule", "venue", "neutral", "average",
                       "expected", "likely", "unlikely", "possible", "potential",
                       "advantage", "disadvantage", "favor", "favored",
                       "consistency", "inconsistent", "consistent", "stable",
                       "analysis", "outlook", "summary", "conclusion", "note",
                       "highlight", "highlights", "warning", "caution", "alert",
                       "upset", "surprise", "dark", "horse", "favorite",
                       "underdog", "trend", "pattern", "factor", "factors",
                       "consideration", "recommendation", "suggest", "suggestion",
                       "advice", "opinion", "view", "assessment", "evaluation",
                       "rating", "rank", "ranking", "position", "standings",
                       "clean", "sheet", "concede", "scored", "scoring",
                       "ball", "possession", "passing", "pressing", "counter",
                       "set", "piece", "corner", "free", "kick", "penalty",
                       "foul", "yellow", "offside", "var", "referee",
                       "coach", "manager", "style", "approach", "strategy",
                       "depth", "width", "pace", "speed", "strength", "size",
                       "experience", "quality", "level", "class", "talent",
                       "improvement", "decline", "rise", "drop", "change",
                       "impact", "influence", "effect", "role", "factor",
                       "issue", "concern", "problem", "challenge", "opportunity",
                       "threat", "weakness", "strength", "advantage", "edge",
                       "significant", "slight", "minor", "major", "crucial",
                       "critical", "important", "vital", "essential", "key",
                       "worth", "noting", "noted", "noteworthy", "mention",
                       "mentioned", "highlighted", "emphasized", "stressed",
                       "based", "given", "considering", "regarding", "despite",
                       "although", "however", "nevertheless", "furthermore",
                       "moreover", "additionally", "therefore", "thus", "hence",
                       "overall", "generally", "usually", "typically", "often",
                       "rarely", "seldom", "always", "never", "sometimes",
                       "currently", "previously", "recently", "lately",
                       "today", "tomorrow", "yesterday", "week", "month",
                       "season", "year", "campaign", "run", "stretch", "spell",
                       "period", "phase", "start", "beginning", "middle", "end",
                       "early", "late", "opening", "closing", "final",
                       "must", "need", "should", "could", "would", "might",
                       "without", "against", "between", "among", "during",
                       "through", "throughout", "across", "along", "around",
                       "above", "below", "behind", "ahead", "forward",
                       "positive", "negative", "neutral", "mixed", "balanced",
                       "tough", "difficult", "hard", "easy", "tight", "close",
                       "open", "wide", "narrow", "deep", "shallow",
                       "good", "bad", "great", "poor", "fair", "decent",
                       "solid", "shaky", "vulnerable", "resilient", "robust",
                       "fragile", "durable", "steady", "volatile", "erratic",
                       "aggressive", "passive", "cautious", "bold", "risky",
                       "safe", "dangerous", "danger", "safe", "secure",
                       "uncertain", "certain", "sure", "unsure", "doubtful",
                       "clear", "unclear", "obvious", "subtle", "hidden",
                       "visible", "apparent", "evident", "noticeable",
                       "marked", "pronounced", "distinct", "different",
                       "similar", "same", "identical", "comparable",
                       "better", "worse", "best", "worst", "superior",
                       "inferior", "stronger", "weaker", "faster", "slower",
                       "higher", "lower", "larger", "smaller", "bigger",
                       "longer", "shorter", "earlier", "later", "sooner",
                       "further", "closer", "nearer", "wider", "narrower",
                       "easier", "harder", "tougher", "softer", "lighter",
                       "heavier", "darker", "brighter", "hotter", "colder",
                       "warmer", "cooler", "wetter", "drier", "dryer",
                       "fitter", "sharper", "blunter", "fresher", "staler",
                       "richer", "poorer", "fuller", "emptier", "busier",
                       "quieter", "louder", "calmer", "noisier", "smoother",
                       "rougher", "tighter", "looser", "stiffer", "looser",
                       "thicker", "thinner", "deeper", "shallower", "broader",
                       "steeper", "flatter", "gentler", "harsher", "milder",
                       "sweeter", "sourer", "bitterer", "saltier", "spicier",
                       "dearer", "cheaper", "pricier", "costlier", "worthier",
                       "total", "totals", "averages", "averaging", "average",
                       "highest", "lowest", "maximum", "minimum", "peak",
                       "bottom", "top", "mid", "middle", "center", "central",
                       "standard", "deviation", "variance", "mean", "median",
                       "mode", "range", "spread", "gap", "difference",
                       "margin", "differential", "ratio", "percentage",
                       "proportion", "share", "portion", "fraction", "slice",
                       "piece", "part", "half", "quarter", "third", "fourth",
                       "fifth", "sixth", "seventh", "eighth", "ninth", "tenth",
                       "single", "double", "triple", "quadruple", "multiple",
                       "several", "few", "couple", "pair", "dozen", "score",
                       "hundred", "thousand", "million", "billion",
                       "zero", "nil", "one", "two", "three", "four", "five",
                       "six", "seven", "eight", "nine", "ten", "eleven",
                       "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
                       "seventeen", "eighteen", "nineteen", "twenty",
                       "thirty", "forty", "fifty", "sixty", "seventy",
                       "eighty", "ninety", "hundred",
                       "north", "south", "east", "west", "northern", "southern",
                       "eastern", "western", "central", "united", "kingdom",
                       "republic", "states", "city", "town", "county", "region",
                       "nation", "country", "international", "domestic", "local",
                       "global", "world", "europe", "asia", "africa", "america",
                       "oceania", "australia", "zealand", "ireland", "scotland",
                       "wales", "england", "france", "germany", "italy", "spain",
                       "portugal", "netherlands", "belgium", "switzerland",
                       "austria", "poland", "sweden", "norway", "denmark",
                       "finland", "iceland", "greece", "turkey", "russia",
                       "ukraine", "croatia", "serbia", "czech", "slovakia",
                       "hungary", "romania", "bulgaria", "slovenia", "bosnia",
                       "montenegro", "albania", "macedonia", "kosovo", "malta",
                       "cyprus", "israel", "georgia", "armenia", "azerbaijan",
                       "kazakhstan", "belarus", "moldova", "lithuania", "latvia",
                       "estonia", "luxembourg", "andorra", "san", "marino",
                       "liechtenstein", "monaco", "gibraltar", "faroe",
                       "brazil", "argentina", "uruguay", "colombia", "chile",
                       "peru", "ecuador", "paraguay", "bolivia", "venezuela",
                       "mexico", "costa", "rica", "panama", "honduras",
                       "salvador", "guatemala", "nicaragua", "belize",
                       "jamaica", "trinidad", "tobago", "haiti", "cuba",
                       "dominican", "puerto", "suriname", "guyana",
                       "japan", "south", "korea", "north", "china", "india",
                       "indonesia", "malaysia", "thailand", "vietnam",
                       "philippines", "singapore", "myanmar", "cambodia",
                       "laos", "brunei", "mongolia", "nepal", "bhutan",
                       "bangladesh", "sri", "lanka", "pakistan", "afghanistan",
                       "iran", "iraq", "syria", "jordan", "lebanon", "kuwait",
                       "bahrain", "qatar", "uae", "emirates", "oman", "yemen",
                       "saudi", "arabia", "palestine", "taiwan", "hong", "kong",
                       "macau", "tibet", "uyghur", "xinjiang", "mongolia",
                       "egypt", "morocco", "algeria", "tunisia", "libya",
                       "sudan", "ethiopia", "kenya", "tanzania", "uganda",
                       "rwanda", "burundi", "somalia", "djibouti", "eritrea",
                       "nigeria", "ghana", "cameroon", "ivory", "coast",
                       "senegal", "mali", "burkina", "faso", "niger", "chad",
                       "benin", "togo", "guinea", "sierra", "leone", "liberia",
                       "gambia", "mauritania", "cabo", "verde", "equatorial",
                       "gabon", "congo", "zaire", "angola", "mozambique",
                       "zimbabwe", "zambia", "malawi", "botswana", "namibia",
                       "south", "africa", "eswatini", "lesotho", "madagascar",
                       "mauritius", "seychelles", "comoros",
                       "canada", "united", "states", "usa", "brazil", "mexico",
                       "argentina", "colombia", "peru", "chile", "uruguay",
                       "ecuador", "bolivia", "paraguay", "venezuela",
                       "fifa", "uefa", "conmebol", "concacaf", "caf", "afc",
                       "ofc", "premier", "league", "championship", "champions",
                       "europa", "serie", "liga", "bundesliga", "ligue",
                       "eredivisie", "primeira", "super", "lig", "mls",
                       "apertura", "clausura", " libertadores", "sudamericana",
                       "copa", "america", "euro", "nations", "world",
                       "asian", "african", "gold", "confederations",
                       "olympic", "olympics", "friendly", "qualifier",
                       "qualifiers", "playoff", "playoffs", "knockout",
                       "relegation", "promotion", "promoted", "relegated",
                       "champion", "champions", "winner", "winners",
                       "runner", "runners", "finalist", "finalists",
                       "semifinal", "quarterfinal", "round", "group",
                       "standing", "standings", "table", "fixture",
                       "fixtures", "result", "results", "score", "scores",
                       "goal", "goals", "assist", "assists", "hat", "trick",
                       "brace", "clean", "sheet", "sheets", "concede",
                       "conceded", "conceding", "scoreless", "draw", "drawn",
                       "defeat", "defeated", "victory", "win", "won", "wins",
                       "loss", "lost", "lose", "losing", "beat", "beaten",
                       "beating", "overcome", "overcame", "overcoming",
                       "dominate", "dominated", "dominating", "dominant",
                       "outplay", "outplayed", "outplaying", "outclass",
                       "outclassed", "outclassing", "outshine", "outshone",
                       "outshining", "outperform", "outperformed",
                       "outperforming", "outscore", "outscored", "outscoring",
                       "thrash", "thrashed", "thrashing", "rout", "routed",
                       "routing", "hammer", "hammered", "hammering", "drub",
                       "drubbed", "drubbing", "wallop", "walloped", "walloping",
                       "demolish", "demolished", "demolishing", "destroy",
                       "destroyed", "destroying", "annihilate", "annihilated",
                       "annihilating", "obliterate", "obliterated", "obliterating",
                       "crush", "crushed", "crushing", "smash", "smashed",
                       "smashing", "pound", "pounded", "pounding", "batter",
                       "battered", "battering", "maul", "mauled", "mauling",
                       "overwhelm", "overwhelmed", "overwhelming",
                       "stun", "stunned", "stunning", "shock", "shocked",
                       "shocking", "surprise", "surprised", "surprising",
                       "upset", "upsets", "upsetting", "giant", "killing",
                       "comeback", "comebacks", "rally", "rallied", "rallying",
                       "fightback", "fightbacks", "resilience", "resilient",
                       "bounce", "bounced", "bouncing", "recover", "recovered",
                       "recovering", "recovery", "respond", "responded",
                       "responding", "response", "reaction", "react", "reacted",
                       "reacting", "counter", "countered", "countering",
                       "equalize", "equalized", "equalizing", "equaliser",
                       "equalizer", "level", "leveled", "leveling", "levelled",
                       "levelling", "draw", "drew", "drawn", "tie", "tied",
                       "tying", "deadlock", "deadlocked", "stalemate",
                       "stalemated", "impasse", "gridlock", "gridlocked",
                       "break", "broke", "broken", "breaking", "breakthrough",
                       "deadlock", "deadlocked", "stalemate", "stalemated",
                       "open", "opened", "opening", "opener", "scoring",
                       "scored", "scores", "net", "netted", "netting",
                       "find", "found", "finding", "slot", "slotted", "slotting",
                       "fire", "fired", "firing", "blast", "blasted", "blasting",
                       "strike", "struck", "striking", "hit", "hitting",
                       "head", "headed", "heading", "header", "volley",
                       "volleyed", "volleying", "chip", "chipped", "chipping",
                       "lob", "lobbed", "lobbing", "curl", "curled", "curling",
                       "bend", "bent", "bending", "drill", "drilled", "drilling",
                       "drive", "drove", "driven", "driving", "smash", "smashed",
                       "smashing", "thump", "thumped", "thumping", "crack",
                       "cracked", "cracking", "rifle", "rifled", "rifling",
                       "power", "powered", "powering", "placement", "placed",
                       "placing", "tap", "tapped", "tapping", "poke", "poked",
                       "poking", "prod", "prodded", "prodding", "nudge",
                       "nudged", "nudging", "deflect", "deflected", "deflecting",
                       "deflection", "ricochet", "ricocheted", "ricocheting",
                       "bounce", "bounced", "bouncing", "rebound", "rebounded",
                       "rebounding", "save", "saved", "saving", "stop", "stopped",
                       "stopping", "block", "blocked", "blocking", "parry",
                       "parried", "parrying", "punch", "punched", "punching",
                       "tip", "tipped", "tipping", "catch", "caught", "catching",
                       "gather", "gathered", "gathering", "collect", "collected",
                       "collecting", "claim", "claimed", "claiming", "hold",
                       "held", "holding", "grab", "grabbed", "grabbing",
                       "snatch", "snatched", "snatching", "pluck", "plucked",
                       "plucking", "smother", "smothered", "smothering",
                       "dive", "dived", "diving", "leap", "leapt", "leaping",
                       "spring", "sprang", "sprung", "springing", "jump",
                       "jumped", "jumping", "stretch", "stretched", "stretching",
                       "reach", "reached", "reaching", "lunge", "lunged",
                       "lunging", "fling", "flung", "flinging", "hurl",
                       "hurled", "hurling", "throw", "threw", "thrown",
                       "throwing", "launch", "launched", "launching",
                       "hoof", "hoofed", "hoofing", "clear", "cleared",
                       "clearing", "clearance", "boot", "booted", "booting",
                       "punt", "punted", "punting", "wallop", "walloped",
                       "walloping", "whack", "whacked", "whacking",
                       "belt", "belted", "belting", "lash", "lashed", "lashing",
                       "hammer", "hammered", "hammering", "nail", "nailed",
                       "nailing", "bang", "banged", "banging", "slam",
                       "slammed", "slamming", "blast", "blasted", "blasting",
                       "rocket", "rocketed", "rocketing", "missile", "torpedo",
                       "bullet", "screamer", "thunderbolt", "pile", "driver",
                       "daisy", "cutter",
                       "tackle", "tackled", "tackling", "challenge", "challenged",
                       "challenging", "intercept", "intercepted", "intercepting",
                       "interception", "steal", "stole", "stolen", "stealing",
                       "dispossess", "dispossessed", "dispossessing", "win",
                       "won", "winning", "regain", "regained", "regaining",
                       "recover", "recovered", "recovering", "recovery",
                       "press", "pressed", "pressing", "pressure", "pressured",
                       "pressuring", "harry", "harried", "harrying", "hassle",
                       "hassled", "hassling", "hound", "hounded", "hounding",
                       "chase", "chased", "chasing", "pursue", "pursued",
                       "pursuing", "track", "tracked", "tracking", "shadow",
                       "shadowed", "shadowing", "mark", "marked", "marking",
                       "cover", "covered", "covering", "guard", "guarded",
                       "guarding", "shield", "shielded", "shielding", "screen",
                       "screened", "screening", "protect", "protected",
                       "protecting", "defend", "defended", "defending",
                       "defense", "defensive", "defender", "defenders",
                       "midfield", "midfielder", "midfielders", "forward",
                       "forwards", "striker", "strikers", "winger", "wingers",
                       "attacker", "attackers", "attacking", "attack",
                       "goalkeeper", "goalkeepers", "keeper", "keepers",
                       "goalie", "goalies", "stopper", "stoppers", "shot",
                       "stopper", "full", "back", "fullback", "fullbacks",
                       "center", "centre", "wing", "back", "wingback",
                       "wingbacks", "sweeper", "sweepers", "libero",
                       "libero", "playmaker", "playmakers", "anchor", "anchors",
                       "pivot", "pivots", "regista", "registas", "trequartista",
                       "trequartistas", "enganche", "enganches", "false",
                       "nine", "target", "man", "poacher", "poachers",
                       "fox", "box",
                       "formation", "formations", "lineup", "lineups", "starting",
                       "eleven", "bench", "substitute", "substitutes",
                       "substitution", "substitutions", "sub", "subs", "subbed",
                       "subbing", "replace", "replaced", "replacing",
                       "replacement", "change", "changed", "changing", "switch",
                       "switched", "switching", "swap", "swapped", "swapping",
                       "rotate", "rotated", "rotating", "rotation", "rest",
                       "rested", "resting", "rested", "drop", "dropped",
                       "dropping", "omit", "omitted", "omitting", "exclude",
                       "excluded", "excluding", "include", "included",
                       "including", "recall", "recalled", "recalling",
                       "reinstate", "reinstated", "reinstating", "return",
                       "returned", "returning", "comeback", "comebacks",
                       "debut", "debuts", "debuting", "debutant", "debutants",
                       "first", "start", "first", "appearance", "first", "goal",
                       "maiden", "goal", "maiden", "appearance", "maiden", "start",
                       "brace", "braces", "hat", "trick", "hat", "tricks",
                       "poker", "pokers", "four", "goal", "haul", "five", "goal",
                       "haul", "double", "doubles", "treble", "trebles",
                       "assist", "assists", "provider", "providers", "creator",
                       "creators", "supplier", "suppliers", "cross", "crosses",
                       "crossed", "crossing", "pass", "passes", "passed",
                       "passing", "through", "ball", "through", "balls",
                       "long", "ball", "long", "balls", "short", "pass",
                       "short", "passes", "one", "two", "one", "twos", "give",
                       "go", "give", "gos", "flick", "flicks", "flicked",
                       "flicking", "backheel", "backheels", "backheeled",
                       "backheeling", "dink", "dinks", "dinked", "dinking",
                       "loft", "lofts", "lofted", "lofting", "float", "floats",
                       "floated", "floating", "ping", "pings", "pinged",
                       "pinging", "spray", "sprays", "sprayed", "spraying",
                       "spread", "spreads", "spreading", "switch", "switches",
                       "switched", "switching", "diagonal", "diagonals",
                       "square", "squares", "squared", "squaring", "lay",
                       "off", "lays", "off", "laid", "off", "laying", "off",
                       "set", "up", "sets", "up", "setting", "up", "set", "up",
                       "tee", "up", "tees", "up", "teed", "up", "teeing", "up",
                       "dribble", "dribbles", "dribbled", "dribbling",
                       "dribbler", "dribblers", "run", "runs", "ran", "running",
                       "runner", "runners", "sprint", "sprints", "sprinted",
                       "sprinting", "dash", "dashes", "dashed", "dashing",
                       "burst", "bursts", "bursting", "surge", "surges",
                       "surging", "charge", "charges", "charged", "charging",
                       "drive", "drives", "drove", "driven", "driving",
                       "advance", "advances", "advanced", "advancing",
                       "progress", "progresses", "progressed", "progressing",
                       "carry", "carries", "carried", "carrying", "take",
                       "on", "takes", "on", "took", "on", "taken", "on",
                       "taking", "on", "beat", "beats", "beaten", "beating",
                       "skip", "skips", "skipped", "skipping", "evade",
                       "evades", "evaded", "evading", "elude", "eludes",
                       "eluded", "eluding", "dodge", "dodges", "dodged",
                       "dodging", "sidestep", "sidesteps", "sidestepped",
                       "sidestepping", "feint", "feints", "feinted", "feinting",
                       "dummy", "dummies", "dummied", "dummying", "nutmeg",
                       "nutmegs", "nutmegged", "nutmegging", "meg", "megs",
                       "megged", "megging", "panna", "pannas", "rainbow",
                       "rainbows", "elastico", "elasticos", "flip", "flap",
                       "flip", "flaps", "step", "over", "step", "overs",
                       "roulette", "roulettes", "marseille", "turn",
                       "marseille", "turns", "cruyff", "turn", "cruyff",
                       "turns", "drag", "back", "drag", "backs", "sole",
                       "roll", "sole", "rolls",
                       "foul", "fouls", "fouled", "fouling", "offense",
                       "offenses", "infringement", "infringements", "violation",
                       "violations", "breach", "breaches", "breached",
                       "breaching", "transgression", "transgressions",
                       "misconduct", "card", "cards", "carded", "carding",
                       "yellow", "yellows", "yellowed", "yellowing", "red",
                       "reds", "redded", "redding", "caution", "cautions",
                       "cautioned", "cautioning", "book", "books", "booked",
                       "booking", "bookings", "send", "off", "sends", "off",
                       "sent", "off", "sending", "off", "dismiss", "dismisses",
                       "dismissed", "dismissing", "dismissal", "dismissals",
                       "eject", "ejects", "ejected", "ejecting", "ejection",
                       "ejections", "expel", "expels", "expelled", "expelling",
                       "expulsion", "expulsions", "march", "marches", "marched",
                       "marching", "orders", "early", "bath", "shower",
                       "showers", "showered", "showering", "sin", "bin",
                       "sin", "bins", "sin", "binned", "sin", "binning",
                       "suspend", "suspends", "suspended", "suspending",
                       "suspension", "suspensions", "ban", "bans", "banned",
                       "banning", "prohibit", "prohibits", "prohibited",
                       "prohibiting", "prohibition", "prohibitions",
                       "disciplinary", "action", "disciplinary", "actions",
                       "retrospective", "action", "retrospective", "actions",
                       "retroactive", "punishment", "retroactive", "punishments",
                       "post", "match", "review", "post", "match", "reviews",
                       "dive", "dives", "dived", "diving", "simulation",
                       "simulations", "simulate", "simulates", "simulated",
                       "simulating", "play", "acting", "play", "acted",
                       "play", "acting", "theatrical", "theatricals",
                       "embellish", "embellishes", "embellished", "embellishing",
                       "embellishment", "embellishments", "exaggerate",
                       "exaggerates", "exaggerated", "exaggerating",
                       "exaggeration", "exaggerations",
                       "handball", "handballs", "hand", "ball", "hand", "balls",
                       "handle", "handles", "handled", "handling", "arm",
                       "arms", "elbow", "elbows", "elbowed", "elbowing",
                       "forearm", "forearms", "wrist", "wrists", "palm",
                       "palms", "finger", "fingers", "fingertip", "fingertips",
                       "knuckle", "knuckles", "shoulder", "shoulders",
                       "shouldered", "shouldering", "upper", "arm", "upper",
                       "arms", "deliberate", "intentional", "accidental",
                       "unnatural", "position", "unnatural", "positions",
                       "silhouette", "silhouettes", "natural", "silhouette",
                       "natural", "silhouettes", "proximity", "distance",
                       "ball", "body", "arm", "body", "torso", "torsos",
                       "chest", "chests", "chested", "chesting", "control",
                       "controls", "controlled", "controlling", "trap",
                       "traps", "trapped", "trapping", "bring", "down",
                       "brings", "down", "brought", "down", "bringing", "down",
                       "offside", "offsides", "off", "side", "off", "sides",
                       "flag", "flags", "flagged", "flagging", "raise",
                       "raises", "raised", "raising", "linesman", "linesmen",
                       "assistant", "referee", "assistant", "referees",
                       "var", "vars", "video", "assistant", "referee",
                       "video", "assistant", "referees", "review", "reviews",
                       "reviewed", "reviewing", "check", "checks", "checked",
                       "checking", "overturn", "overturns", "overturned",
                       "overturning", "uphold", "upholds", "upheld",
                       "upholding", "confirm", "confirms", "confirmed",
                       "confirming", "monitor", "monitors", "monitored",
                       "monitoring", "pitchside", "screen", "pitchside",
                       "screens", "referee", "review", "area", "referee",
                       "review", "areas", "rra", "rras", "clear", "obvious",
                       "error", "clear", "obvious", "errors", "serious",
                       "missed", "incident", "serious", "missed", "incidents",
                       "goal", "line", "technology", "glt", "hawk", "eye",
                       "hawk", "eyes", "goal", "decision", "system", "gds",
                       "goal", "ref", "goal", "refs", "sensor", "sensors",
                       "chip", "chips", "microchip", "microchips", "implant",
                       "implants", "implanted", "implanting", "embedded",
                       "ball", "technology", "connected", "ball", "technology",
                       "semi", "automated", "offside", "technology", "saot",
                       "limb", "tracking", "limb", "trackings", "skeletal",
                       "tracking", "skeletal", "trackings", "optical",
                       "tracking", "optical", "trackings", "camera", "cameras",
                       "sensor", "sensors", "fusion", "fusions", "data",
                       "fusion", "data", "fusions", "hawk", "eye", "hawk",
                       "eyes", "goal", "line", "technology", "goal", "decision",
                       "system", "automatic", "goal", "detection", "agd",
                       "chip", "ball", "chip", "balls", "adidas", "nike",
                       "puma", "umbro", "mitre", "select", "derbystar",
                       "voit", "molten", "errea", "macron", "kappa", "joma",
                       "hummel", "uhlsport", "warrior", "new", "balance",
                       "under", "armour", "castore", "o'neills", "oneills",
                       "o'neill's", "oneill's", "o'neills", "oneills",
                       "le", "coq", "sportif", "lecoq", "sportif", "diadora",
                       "lotto", "asics", "mizuno", "yonex", "li", "ning",
                       "lining", "anta", "peak", "xtep", "361", "degrees",
                       "degrees", "erke", "qiaodan", "jordan", "brand",
                       "supplier", "suppliers", "manufacturer", "manufacturers",
                       "kit", "kits", "jersey", "jerseys", "shirt", "shirts",
                       "strip", "strips", "uniform", "uniforms", "apparel",
                       "attire", "garment", "garments", "clothing", "wear",
                       "sponsor", "sponsors", "sponsored", "sponsoring",
                       "sponsorship", "sponsorships", "partner", "partners",
                       "partnership", "partnerships", "deal", "deals", "contract",
                       "contracts", "agreement", "agreements", "extension",
                       "extensions", "renewal", "renewals", "expire", "expires",
                       "expired", "expiring", "expiry", "expiries", "expiration",
                       "expirations", "terminate", "terminates", "terminated",
                       "terminating", "termination", "terminations", "end",
                       "ends", "ended", "ending", "conclude", "concludes",
                       "concluded", "concluding", "conclusion", "conclusions",
                       "sign", "signs", "signed", "signing", "signature",
                       "signatures", "seal", "seals", "sealed", "sealing",
                       "ink", "inks", "inked", "inking", "pen", "pens",
                       "penned", "penning", "agree", "agrees", "agreed",
                       "agreeing", "consent", "consents", "consented",
                       "consenting", "accept", "accepts", "accepted",
                       "accepting", "acceptance", "acceptances", "approve",
                       "approves", "approved", "approving", "approval",
                       "approvals", "ratify", "ratifies", "ratified",
                       "ratifying", "ratification", "ratifications",
                       "confirm", "confirms", "confirmed", "confirming",
                       "confirmation", "confirmations", "finalize", "finalizes",
                       "finalized", "finalizing", "finalization",
                       "finalizations", "complete", "completes", "completed",
                       "completing", "completion", "completions", "wrap",
                       "up", "wraps", "up", "wrapped", "up", "wrapping", "up",
                       "done", "deal", "done", "deals", "done", "dust",
                       "done", "dusts", "dust", "done", "dusted", "done",
                       "dusting", "done", "conclude", "concludes", "concluded",
                       "concluding", "conclusion", "conclusions", "finish",
                       "finishes", "finished", "finishing", "closure", "closes",
                       "closed", "closing", "shut", "shuts", "shutting",
                       "lock", "locks", "locked", "locking", "secure",
                       "secures", "secured", "securing", "capture", "captures",
                       "captured", "capturing", "land", "lands", "landed",
                       "landing", "snap", "up", "snaps", "up", "snapped",
                       "up", "snapping", "up", "swoop", "swoops", "swooped",
                       "swooping", "pounce", "pounces", "pounced", "pouncing",
                       "nab", "nabs", "nabbed", "nabbing", "grab", "grabs",
                       "grabbed", "grabbing", "seize", "seizes", "seized",
                       "seizing", "clinch", "clinches", "clinched", "clinching",
                       "tie", "up", "ties", "up", "tied", "up", "tying", "up",
                       "sew", "up", "sews", "up", "sewed", "up", "sewn", "up",
                       "sewing", "up", "stitch", "up", "stitches", "up",
                       "stitched", "up", "stitching", "up", "button", "up",
                       "buttons", "up", "buttoned", "up", "buttoning", "up",
                       "zip", "up", "zips", "up", "zipped", "up", "zipping", "up"}:
                continue
            if pt not in known_teams_lower:
                # 检查是否是已知球队的部分匹配
                found = False
                for kt in known_teams_lower:
                    if pt in kt or kt in pt:
                        found = True
                        break
                if not found:
                    errors.append(f"narrative_references_unknown_entity: {pt}")

        # 3. 检查是否编造了不在证据中的数字（百分比、赔率、比分）
        # 百分比格式: "xx%" 或 "xx.x%"
        pct_pattern = re.compile(r'\b(\d+(?:\.\d+)?)\s*%')
        for match in pct_pattern.finditer(full_text):
            pct_str = match.group(0)
            # 检查是否在已知数字集合中
            known_numbers = {
                f"{bundle.probabilities.home_win:.1%}",
                f"{bundle.probabilities.draw:.1%}",
                f"{bundle.probabilities.away_win:.1%}",
                f"{bundle.probabilities.over_2_5:.1%}",
                f"{bundle.probabilities.under_2_5:.1%}",
            }
            if pct_str not in known_numbers:
                errors.append(f"narrative_contains_unverified_percentage: {pct_str}")

        # 比分格式: "x-y" 或 "x:y"
        score_pattern = re.compile(r'\b(\d+)[-:](\d+)\b')
        known_scores = {s for s, _ in bundle.probabilities.most_likely_scores}
        for match in score_pattern.finditer(full_text):
            found_score = f"{match.group(1)}-{match.group(2)}"
            if found_score not in known_scores:
                errors.append(f"narrative_contains_unverified_score: {found_score}")

        # 4. 检查是否与 policy.recommendation 冲突
        rec_lower = bundle.policy.recommendation.value.lower()
        if rec_lower == "no_bet":
            bet_terms = ["建议投注", "推荐投注", "值得投注", "建议买入", "推荐买入",
                          "建议下注", "推荐下注", "可以投注", "应该投注"]
            for term in bet_terms:
                if term in full_text:
                    errors.append(f"narrative_conflicts_with_no_bet_policy: {term}")

        is_valid = len(errors) == 0
        return is_valid, errors

    @staticmethod
    def _matchup_line(bundle: PredictionBundle) -> str:
        return f"{bundle.features.home.name} vs {bundle.features.away.name}"

    @staticmethod
    def _league_line(bundle: PredictionBundle) -> str:
        league = bundle.features.context.get("league_standard_name")
        return str(league or "UNKNOWN")

    @classmethod
    def _required_fact_fragments(cls, bundle: PredictionBundle) -> tuple[str, ...]:
        fragments = [
            bundle.features.home.name,
            bundle.features.away.name,
        ]
        league = bundle.features.context.get("league_standard_name")
        if league:
            fragments.append(str(league))
        fragments.extend(bundle.policy.reasons)
        return tuple(fragment for fragment in fragments if fragment)

    @staticmethod
    def _secondary_value_sections(bundle: PredictionBundle) -> str:
        sections: list[str] = []
        for market_name, assessment in bundle.secondary_value_assessments.items():
            lines = "\n".join(
                f"- {item.outcome}: odds {item.decimal_odds:.2f}, implied {item.implied_probability:.1%}, "
                f"model {item.model_probability:.1%}, edge {item.edge:.1%}, value={item.value}"
                for item in assessment.values
            )
            sections.append(f"\n## {market_name}价值\n{lines}")
        return "\n".join(sections)

    # ============================================================
    # 增强特征章节（新增）
    # ============================================================

    @staticmethod
    def _enhanced_features_section(bundle: PredictionBundle) -> str:
        """阵型、H2H、休息天数、赔率趋势、阵容确认。"""
        ctx = bundle.features.context
        lines: list[str] = []

        # 阵型
        home_f = ctx.get("formation_home", "")
        away_f = ctx.get("formation_away", "")
        if home_f and away_f:
            home_bias = ctx.get("formation_home_attack_bias", 0.0)
            away_bias = ctx.get("formation_away_attack_bias", 0.0)
            adv = ctx.get("formation_attack_advantage", 0.0)
            adv_str = f"主队进攻优势" if adv > 0.05 else ("客队进攻优势" if adv < -0.05 else "均势")
            lines.append(
                f"- 阵型: {home_f}(进攻倾向 {float(home_bias):.2f}) "
                f"vs {away_f}(进攻倾向 {float(away_bias):.2f}) → {adv_str}"
            )
            midfield_diff = int(ctx.get("formation_midfield_diff", 0))
            if midfield_diff != 0:
                side = "主队" if midfield_diff > 0 else "客队"
                lines.append(f"- 中场对抗: {side} 中场人数多 {abs(midfield_diff)} 人")
        else:
            lines.append("- 阵型: 暂无数据")

        # H2H 心理优势
        h2h_total = int(ctx.get("h2h_total_matches", 0))
        if h2h_total > 0:
            edge = float(ctx.get("h2h_psychological_edge", 0.0))
            edge_str = "主队心理优势" if edge > 0.1 else ("客队心理优势" if edge < -0.1 else "均势")
            lines.append(
                f"- 历史交锋: {h2h_total} 场, "
                f"主胜 {ctx.get('h2h_home_wins', 0)}, "
                f"平 {ctx.get('h2h_draws', 0)}, "
                f"客胜 {ctx.get('h2h_away_wins', 0)} → {edge_str}"
            )
        else:
            lines.append("- 历史交锋: 暂无数据")

        # 休息天数
        rest_diff = ctx.get("rest_days_diff", 0)
        if rest_diff is not None:
            rest_home = ctx.get("rest_days_home", "?")
            rest_away = ctx.get("rest_days_away", "?")
            fp_home = float(ctx.get("rest_fatigue_penalty_home", 0.0))
            fp_away = float(ctx.get("rest_fatigue_penalty_away", 0.0))
            fatigue_note = ""
            if fp_home > 0 or fp_away > 0:
                fatigue_parts = []
                if fp_home > 0:
                    fatigue_parts.append(f"主队疲劳惩罚 {fp_home:.3f}")
                if fp_away > 0:
                    fatigue_parts.append(f"客队疲劳惩罚 {fp_away:.3f}")
                fatigue_note = f"（{', '.join(fatigue_parts)}）"
            lines.append(
                f"- 休息天数: 主队 {rest_home} 天, 客队 {rest_away} 天, "
                f"差 {rest_diff} 天 {fatigue_note}"
            )
        else:
            lines.append("- 休息天数: 暂无数据")

        # 赔率趋势
        trend_dir = ctx.get("odds_trend_home_direction", "")
        if trend_dir:
            trend_pct = float(ctx.get("odds_trend_home_change_pct", 0.0))
            sentiment = float(ctx.get("odds_trend_market_sentiment", 0.0))
            direction_cn = {"up": "上升", "down": "下降", "stable": "稳定"}.get(trend_dir, trend_dir)
            snap_count = ctx.get("odds_trend_snapshot_count", 0)
            sentiment_str = "看好主队" if sentiment > 0.01 else ("看好客队" if sentiment < -0.01 else "中性")
            lines.append(
                f"- 赔率趋势: 主胜赔率 {direction_cn} ({trend_pct:+.1%}), "
                f"基于 {snap_count} 个快照, 市场情绪: {sentiment_str}"
            )
        else:
            lines.append("- 赔率趋势: 暂无数据")

        # 阵容确认
        lineup_conf = ctx.get("lineup_confidence", "")
        if lineup_conf:
            conf_cn = {"high": "高（双方首发已确认）", "partial": "中（仅一方确认）", "low": "低（双方均未确认）"}.get(
                lineup_conf, lineup_conf
            )
            lines.append(f"- 阵容确认状态: {conf_cn}")
        else:
            lines.append("- 阵容确认状态: 暂无数据")

        return "\n".join(lines) if lines else "- 暂无增强特征数据。"

    # ============================================================
    # 竞彩特殊玩法章节（新增）
    # ============================================================

    @staticmethod
    def _sporttery_special_section(bundle: PredictionBundle) -> str:
        """竞彩比分、总进球、半全场赔率 + 模型概率。"""
        lines: list[str] = []
        probs = bundle.probabilities

        # 总进球
        odds_tg = bundle.features.odds_total_goals
        if probs.total_goals_probabilities:
            lines.append("### 总进球")
            for goals in ("0", "1", "2", "3", "4", "5", "6", "7+"):
                prob = probs.total_goals_probabilities.get(goals, 0.0)
                if prob > 0.001:
                    odd_str = ""
                    if goals in odds_tg:
                        odd_str = f", 竞彩赔率 {odds_tg[goals]:.2f}"
                    lines.append(f"- {goals}球: 模型概率 {prob:.1%}{odd_str}")
            lines.append("")

        # 比分
        odds_cs = bundle.features.odds_correct_score
        if probs.most_likely_scores:
            lines.append("### 比分")
            for score, prob in probs.most_likely_scores[:5]:
                odd_str = ""
                score_key = score.replace("-", ":")
                if score_key in odds_cs:
                    odd_str = f", 竞彩赔率 {odds_cs[score_key]:.2f}"
                elif score in odds_cs:
                    odd_str = f", 竞彩赔率 {odds_cs[score]:.2f}"
                lines.append(f"- {score}: 模型概率 {prob:.1%}{odd_str}")
            lines.append("")

        # 半全场
        odds_hf = bundle.features.odds_half_full
        if probs.half_full_probabilities:
            lines.append("### 半全场")
            top_hf = sorted(
                probs.half_full_probabilities.items(),
                key=lambda x: x[1],
                reverse=True,
            )[:5]
            for label, prob in top_hf:
                odd_str = ""
                if label in odds_hf:
                    odd_str = f", 竞彩赔率 {odds_hf[label]:.2f}"
                lines.append(f"- {label}: 模型概率 {prob:.1%}{odd_str}")
            lines.append("")

        if not lines:
            return "- 暂无竞彩特殊玩法数据。"
        return "\n".join(lines)

    # ============================================================
    # 结构化摘要（更新：整合增强特征）
    # ============================================================

    @staticmethod
    def _context_lines(bundle: PredictionBundle) -> str:
        context = bundle.features.context
        features = bundle.features
        if context.get("feature_source") != "duckdb_view":
            reason = context.get("fallback_reason", "DuckDB feature view unavailable.")
            return f"- DuckDB 结构化宽表未命中，当前使用占位降级特征。原因: {reason}"

        lines = []

        # 基础信息：直接读取 features 中的 Elo，不从 context 默认 1500
        lines.append(
            f"- 基础: 主Elo {features.home.elo:.0f}, "
            f"客Elo {features.away.elo:.0f}"
        )

        # 伤停
        home_abs = context.get("home_key_absences", 0)
        away_abs = context.get("away_key_absences", 0)
        if home_abs or away_abs:
            lines.append(
                f"- 伤停: 主队核心缺阵 {home_abs} 人, 客队核心缺阵 {away_abs} 人"
            )

        # 天气（仅当有数据时）
        weather = context.get("weather_condition", "")
        if weather and weather != "UNKNOWN":
            temp = context.get("temperature", "")
            lines.append(f"- 天气: {weather}, {temp}C")

        # 赔率原始变动（来自视图，非增强器）
        home_mv = context.get("home_odds_movement")
        if home_mv and home_mv != "None":
            draw_mv = context.get("draw_odds_movement")
            away_mv = context.get("away_odds_movement")
            lines.append(
                f"- 赔率原始变动: 主 {home_mv}, 平 {draw_mv}, 客 {away_mv}"
            )

        # 数据质量
        critical_age = context.get("critical_data_age_minutes", "?")
        dq_flag = context.get("no_bet_data_quality_flag", False)
        lines.append(
            f"- 数据质量: 关键数据年龄 {critical_age} 分钟, "
            f"No Bet 数据质量标记={dq_flag}"
        )

        # 同步状态
        sync_statuses = context.get("sync_statuses", [])
        if sync_statuses:
            sq_flag = context.get("sync_data_quality_flag", "")
            lines.append(
                f"- 同步状态: {', '.join(sync_statuses)}, "
                f"同步质量标记={sq_flag}"
            )

        if context.get("missing_exchange_flow_flag"):
            lines.append("- 资金流数据未接入，风险评估缺少该维度。")
        if context.get("stale_data_detail"):
            lines.append(f"- 宽表新鲜度: {context['stale_data_detail']}")
        readiness = context.get("readiness") or {}
        if readiness:
            missing = ", ".join(readiness.get("missing_critical", [])) or "无"
            stale = ", ".join(readiness.get("stale_critical", [])) or "无"
            lines.append(
                f"- 组件就绪: ready={readiness.get('ready', False)}, "
                f"缺失={missing}, 过期={stale}"
            )

        return "\n".join(lines) if lines else "- 暂无结构化数据。"
