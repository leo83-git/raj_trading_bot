import ast

with open('core/zerodha_broker.py', 'r') as f:
    tree = ast.parse(f.read())

for node in ast.iter_child_nodes(tree):
    if isinstance(node, ast.ClassDef):
        print(f"Class: {node.name}")
        for child in node.body:
            if isinstance(child, ast.FunctionDef):
                print(f"  - def {child.name}(...)")
    elif isinstance(node, ast.FunctionDef):
        print(f"def {node.name}(...)")
