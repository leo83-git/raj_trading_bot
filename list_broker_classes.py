import ast

with open('sources/broker/__init__.py', 'r') as f:
    tree = ast.parse(f.read())

for node in ast.iter_child_nodes(tree):
    if isinstance(node, ast.ClassDef):
        if node.name == 'ZerodhaBroker':
            print(f"Class: {node.name}")
            for child in node.body:
                if isinstance(child, ast.FunctionDef):
                    print(f"  - def {child.name}(...)")
