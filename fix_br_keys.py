import os

for root, dirs, files in os.walk('.'):
    for f in files:
        if f.endswith('.py'):
            path = os.path.join(root, f)
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Check for various patterns of 'br' key usage
            has_br = False
            patterns = ['p.get("cmp")', "p.get('cmp')", 'line.get("cmp")', "line.get('cmp')", 'row.get("cmp")', "row.get('cmp')", 'part.get("cmp")', "part.get('cmp')"]
            for pattern in patterns:
                if pattern in content:
                    has_br = True
                    break
            
            if has_br:
                print(f"Found 'br' key usage in: {path}")
                # Replace all patterns
                replacements = {
                    'p.get("cmp")': 'p.get("cmp")',
                    "p.get('cmp')": "p.get('cmp')",
                    'line.get("cmp")': 'line.get("cmp")',
                    "line.get('cmp')": "line.get('cmp')",
                    'row.get("cmp")': 'row.get("cmp")',
                    "row.get('cmp')": "row.get('cmp')",
                    'part.get("cmp")': 'part.get("cmp")',
                    "part.get('cmp')": "part.get('cmp')",
                }
                for old, new in replacements.items():
                    content = content.replace(old, new)
                
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(content)
                print(f"  Fixed: {path}")