"""Two-player betting trees.

Player 0 is always hero, player 1 is villain. Amounts are in big blinds and
count only chips put in *during* the tree; money already in the pot when the
tree starts (blinds, the open raise, earlier streets) is ``start_pot``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

HERO, VILLAIN = 0, 1


@dataclass
class Node:
    """A decision node (``player`` is 0/1) or a terminal (``player`` is None).

    Terminals are either a fold (``folder`` set) or a showdown.
    ``invested`` is what each player has added since the tree started.
    """

    path: str
    pot: float
    invested: tuple[float, float]
    player: int | None = None
    actions: list[str] = field(default_factory=list)
    children: list["Node"] = field(default_factory=list)
    folder: int | None = None

    @property
    def is_terminal(self) -> bool:
        return self.player is None

    def walk(self):
        """Yield this node and every node below it."""
        yield self
        for child in self.children:
            yield from child.walk()


def _child_path(parent: str, action: str) -> str:
    return f"{parent}/{action}" if parent else action


def _add(invested: tuple[float, float], player: int, amount: float) -> tuple[float, float]:
    out = list(invested)
    out[player] += amount
    return out[0], out[1]


def build_river_tree(start_pot: float, stack: float, first_to_act: int,
                     bet_fractions: list[float], raise_all_in: bool = True) -> Node:
    """River tree: check/bet, then fold/call/(all-in raise) when facing a bet.

    ``stack`` is the effective stack behind at the start of the river.
    Bets are fractions of the current pot, capped at the stack. After a bet,
    the other player may raise all-in (if ``raise_all_in`` and chips remain);
    an all-in raise can only be called or folded.
    """
    if not bet_fractions:
        raise ValueError("Need at least one bet size")
    if first_to_act not in (HERO, VILLAIN):
        raise ValueError("first_to_act must be 0 (hero) or 1 (villain)")

    def node_at(path: str, invested: tuple[float, float], player: int | None,
                folder: int | None = None) -> Node:
        return Node(path, start_pot + sum(invested), invested, player=player, folder=folder)

    def facing_bet(path: str, invested: tuple[float, float], player: int) -> Node:
        node = node_at(path, invested, player)
        to_call = invested[1 - player] - invested[player]
        node.actions = ["fold", "call"]
        node.children = [node_at(_child_path(path, "fold"), invested, None, folder=player),
                         node_at(_child_path(path, "call"), _add(invested, player, to_call), None)]
        # Once someone is all-in, the stack check below stops further raises.
        if raise_all_in and stack > invested[1 - player]:
            raised = _add(invested, player, stack - invested[player])
            node.actions.append("all-in")
            node.children.append(facing_bet(_child_path(path, "all-in"), raised, 1 - player))
        return node

    def betting(path: str, player: int, can_check_back: bool) -> Node:
        node = node_at(path, (0.0, 0.0), player)
        check_path = _child_path(path, "check")
        if can_check_back:
            check_child = betting(check_path, 1 - player, can_check_back=False)
        else:
            check_child = node_at(check_path, (0.0, 0.0), None)
        node.actions.append("check")
        node.children.append(check_child)
        seen: set[float] = set()
        for fraction in bet_fractions:
            amount = min(round(fraction * start_pot, 2), stack)
            if amount <= 0 or amount in seen:
                continue
            seen.add(amount)
            label = "all-in" if amount >= stack else f"bet {round(fraction * 100)}%"
            child_path = _child_path(path, label)
            node.actions.append(label)
            node.children.append(facing_bet(child_path, _add((0.0, 0.0), player, amount), 1 - player))
        return node

    return betting("", first_to_act, can_check_back=True)
