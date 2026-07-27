sudo find . -type d -name "migrations" -exec find {} -type f ! -name "__init__.py" -delete \;
sudo find . -path "*/migrations" -type d -exec rm -rf {} +
