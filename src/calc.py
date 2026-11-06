"""受控计算器：只允许四则运算与括号，Decimal 精确求值。"""
from __future__ import annotations

import ast
import operator
from decimal import Decimal, InvalidOperation

_BIN = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}
_MAX_LEN = 200


class CalcError(ValueError):
    pass


def evaluate(expr: str) -> Decimal:
    s = (expr or "").strip().replace("，", ",").replace("×", "*").replace("÷", "/")
    if not s or len(s) > _MAX_LEN:
        raise CalcError("表达式为空或过长")
    s = s.replace(",", "")
    try:
        tree = ast.parse(s, mode="eval")
    except SyntaxError as e:
        raise CalcError(f"表达式无法解析：{e.msg}") from e

    def ev(node: ast.AST) -> Decimal:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return Decimal(str(node.value))
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
            return _BIN[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            v = ev(node.operand)
            return -v if isinstance(node.op, ast.USub) else v
        raise CalcError(f"不允许的语法：{type(node).__name__}")

    try:
        value = ev(tree.body)
    except (ZeroDivisionError, InvalidOperation) as e:
        raise CalcError(str(e)) from e
    if not value.is_finite():
        raise CalcError("结果不是有限数")
    return value


def fmt(value: Decimal, places: int = 6) -> str:
    q = value.quantize(Decimal(1).scaleb(-places))
    s = format(q, "f").rstrip("0").rstrip(".")
    return s or "0"


def calculate(expr: str) -> tuple[str, Decimal]:
    v = evaluate(expr)
    return fmt(v), v
