from typing import Optional


class TagMatcher:
    def __init__(self, synonyms: dict[str, list[str]]):
        # 构建反向查找：每个词 → 它所属的"规范词组"集合
        self._groups: dict[str, set[str]] = {}
        for canonical, alts in synonyms.items():
            group = {canonical} | set(alts)
            for word in group:
                self._groups[word] = group

    def _normalize(self, tag: str) -> set[str]:
        """返回该 tag 所在的近义词组（含自身），找不到则返回单元素集合"""
        return self._groups.get(tag, {tag})

    def score(self, query: list[str], anim_tags: list[str]) -> float:
        """
        计算 query 与 anim_tags 的相似度（0.0 ~ 1.0+）。
        对每个 query tag，若 anim_tags 中有同组词则得 1 分；
        若完全一致（精确匹配）额外加 0.1 分，用于平局时优先选择精确匹配。
        """
        if not query:
            return 0.0
        anim_set = set(anim_tags)
        anim_groups: set[str] = set()
        for t in anim_tags:
            anim_groups.update(self._normalize(t))

        EXACT_BONUS = 0.1
        total = 0.0
        for q in query:
            if self._normalize(q) & anim_groups:
                total += 1.0
                if q in anim_set:
                    total += EXACT_BONUS
        return total / len(query)

    def find_best(
        self,
        query: list[str],
        animations: list[dict]
    ) -> Optional[dict]:
        """从动画列表中返回得分最高的动画，列表为空返回 None。"""
        if not animations:
            return None
        return max(animations, key=lambda a: self.score(query, a.get("tags", [])))
