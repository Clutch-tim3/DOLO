import os, shutil

# Create firebase_public directory
dest_dir = "firebase_public"
if os.path.exists(dest_dir):
    shutil.rmtree(dest_dir)
os.makedirs(dest_dir)

# Create static sub-directory inside it
static_dest = os.path.join(dest_dir, "static")
os.makedirs(static_dest)

# Copy files
for filename in os.listdir('static'):
    src = os.path.join('static', filename)
    if os.path.isfile(src):
        if filename.endswith('.html'):
            shutil.copy2(src, os.path.join(dest_dir, filename))
        elif filename.endswith('.css') or filename.endswith('.js'):
            shutil.copy2(src, os.path.join(static_dest, filename))

print("Build complete! Files are ready in firebase_public/")
