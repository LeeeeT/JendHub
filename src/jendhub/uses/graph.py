from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class UseGraph:
    users: dict[str, frozenset[str]]
    used: dict[str, frozenset[str]]

    @staticmethod
    def of(edges: Iterable[tuple[str, str]]) -> UseGraph:
        users: defaultdict[str, set[str]] = defaultdict(set)
        used: defaultdict[str, set[str]] = defaultdict(set)
        for user, target in edges:
            users[target].add(user)
            used[user].add(target)
        return UseGraph(frozen(users), frozen(used))


def frozen(found: dict[str, set[str]]) -> dict[str, frozenset[str]]:
    return {id_: frozenset(ids) for id_, ids in found.items()}


def reached(id_: str, edges: dict[str, frozenset[str]]) -> set[str]:
    seen = {id_}
    stack = [id_]
    while stack:
        for found in edges.get(stack.pop(), frozenset()):
            if found not in seen:
                seen.add(found)
                stack.append(found)
    return seen - {id_}
