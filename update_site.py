import os

html_files = ['index.html', 'about.html', 'features.html', 'faq.html', 'therapy.html']

style_block = """
    <style>
      .nav-logo {
        height: 40px;
        width: auto;
        object-fit: contain;
        vertical-align: middle;
        border-radius: 4px;
      }
      .dropdown-menu, .dropdown-content, .dropdown-item, nav .dropdown a, .dropdown select {
        color: #ffffff !important;
        background-color: #1a1a1a !important;
      }
      small, .small-text, .muted-text, p.subtitle, span.subtext {
        color: #ffffff !important;
      }
    </style>
"""

logo_tag = '<img src="assets/cognicopia-logo.jpg" alt="Cognicopia Logo" class="nav-logo">'

for filename in html_files:
    if not os.path.exists(filename):
        print(f"Skipping {filename} - file not found.")
        continue
        
    with open(filename, 'r', encoding='utf-8') as f:
        content = f.read()
        
    # Inject CSS into head if not already present
    if '.nav-logo' not in content:
        if '<head>' in content:
            content = content.replace('<head>', '<head>\n' + style_block, 1)
        elif '</head>' in content:
            content = content.replace('</head>', style_block + '\n</head>', 1)
            
    # Insert logo into nav if not already present
    if 'class="nav-logo"' not in content:
        if '<nav>' in content:
            content = content.replace('<nav>', '<nav>\n    <a href="index.html">' + logo_tag + '</a>', 1)
        elif '<nav class=' in content:
            nav_idx = content.find('<nav')
            close_idx = content.find('>', nav_idx)
            if close_idx != -1:
                content = content[:close_idx+1] + '\n    <a href="index.html">' + logo_tag + '</a>' + content[close_idx+1:]
                
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(content)
        
    print(f"Successfully updated: {filename}")

print("All HTML files updated successfully!")