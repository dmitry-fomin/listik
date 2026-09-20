"""Планировщик волн для стенда роя.

Только для тестов: настоящий планировщик (`deps.waves`) живёт в `listik/` и пишется
отдельной порцией (swarm-2); этот модуль его не заменяет и в `listik/` не импортируется.

Волна — набор задач, которые можно делать одновременно: у них закрыты все жёсткие
зависимости (`deps`), и они не пишут в одни и те же файлы/каталоги (условие Бернстайна
по `write_scope`) и не делят одно дерево записи (`tree_key`). Пересечения внутри волны
разводятся искусственным ребром `resource-blocks`: более ранняя (по порядку объявления)
задача блокирует более позднюю, и та уезжает в следующую волну.
"""

from __future__ import annotations

from .scenario import Scenario, scopes_intersect, validate


def _dep_incoming(nodes: set[str], by_id: dict) -> dict[str, set[str]]:
    incoming: dict[str, set[str]] = {tid: set() for tid in nodes}
    for tid in nodes:
        for dep in by_id[tid].deps:
            if dep in nodes:
                incoming[tid].add(dep)
    return incoming


def _kahn_layers(
    nodes: set[str],
    ids: list[str],
    by_id: dict,
    resource_incoming: dict[str, set[str]],
) -> tuple[list[list[str]], set[str]]:
    incoming = _dep_incoming(nodes, by_id)
    for tid, preds in resource_incoming.items():
        if tid in incoming:
            incoming[tid].update(p for p in preds if p in nodes)

    rem = set(nodes)
    layers: list[list[str]] = []
    while rem:
        layer = [tid for tid in ids if tid in rem and not (incoming[tid] & rem)]
        if not layer:
            break
        for tid in layer:
            rem.discard(tid)
        layers.append(layer)
    return layers, rem


def _find_cycles(
    rem: set[str],
    ids: list[str],
    by_id: dict,
    order_index: dict[str, int],
) -> list[list[str]]:
    adj: dict[str, list[str]] = {tid: [] for tid in rem}
    for tid in ids:
        if tid not in rem:
            continue
        for dep in by_id[tid].deps:
            if dep in rem:
                adj[dep].append(tid)

    found: set[tuple[str, ...]] = set()

    def canonicalize(cycle: list[str]) -> tuple[str, ...]:
        start = min(range(len(cycle)), key=lambda i: order_index[cycle[i]])
        rotated = cycle[start:] + cycle[:start]
        return tuple(rotated)

    def dfs(start: str, node: str, path: list[str], on_path: set[str]) -> None:
        for nxt in adj.get(node, ()):
            if nxt == start:
                found.add(canonicalize(list(path)))
            elif nxt not in on_path:
                path.append(nxt)
                on_path.add(nxt)
                dfs(start, nxt, path, on_path)
                path.pop()
                on_path.discard(nxt)

    for start in ids:
        if start in rem:
            dfs(start, start, [start], {start})

    return [list(cycle) for cycle in sorted(found, key=lambda c: tuple(order_index[x] for x in c))]


def waves(scenario: Scenario, *, done: set[str] = frozenset(), failed: set[str] = frozenset()) -> dict:
    validate(scenario)

    ids = [task.id for task in scenario.tasks]
    order_index = {tid: i for i, tid in enumerate(ids)}
    by_id = {task.id: task for task in scenario.tasks}

    done = set(done)
    failed = set(failed)

    working = {tid for tid in ids if tid not in done}
    unroutable = [tid for tid in ids if tid in working and by_id[tid].launch_route is None]

    working -= failed
    working -= set(unroutable)

    blocked: dict[str, str] = {}
    changed = True
    while changed:
        changed = False
        for tid in ids:
            if tid not in working:
                continue
            task = by_id[tid]
            candidates = [dep for dep in task.deps if dep not in done and dep not in working]
            if candidates:
                reason = min(candidates, key=lambda d: order_index[d])
                blocked[tid] = reason
                working.discard(tid)
                changed = True

    resource_incoming: dict[str, set[str]] = {}
    layers, rem = _kahn_layers(working, ids, by_id, resource_incoming)

    if rem:
        cycles = _find_cycles(rem, ids, by_id, order_index)
        return {
            "waves": [],
            "cycles": cycles,
            "unroutable": unroutable,
            "blocked": blocked,
            "resource_blocks": [],
        }

    resource_blocks: list[list[str]] = []
    resource_pairs: set[tuple[str, str]] = set()

    while True:
        added = False
        for layer in layers:
            for i, a_id in enumerate(layer):
                for b_id in layer[i + 1 :]:
                    a = by_id[a_id]
                    b = by_id[b_id]
                    conflict = scopes_intersect(a.write_scope, b.write_scope) or (
                        a.tree_key is not None and a.tree_key == b.tree_key
                    )
                    if conflict and (a_id, b_id) not in resource_pairs:
                        resource_pairs.add((a_id, b_id))
                        resource_blocks.append([a_id, b_id])
                        resource_incoming.setdefault(b_id, set()).add(a_id)
                        added = True
                        break
                if added:
                    break
            if added:
                break
        if not added:
            break
        layers, rem = _kahn_layers(working, ids, by_id, resource_incoming)

    return {
        "waves": layers,
        "cycles": [],
        "unroutable": unroutable,
        "blocked": blocked,
        "resource_blocks": resource_blocks,
    }


def ready_now(
    scenario: Scenario, *, done: frozenset[str] = frozenset(), failed: frozenset[str] = frozenset()
) -> list[str]:
    result = waves(scenario, done=done, failed=failed)
    layers = result["waves"]
    return layers[0] if layers else []
