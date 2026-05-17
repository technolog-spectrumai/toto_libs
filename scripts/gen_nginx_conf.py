from jinja2 import Template
import os


TEMPLATE_FILE = "nginx.conf.j2"

def generate_config(domain, ssl_cert, ssl_key, output_file,
                    upstream_host="web_toto", upstream_port=8000):
    template_path = os.path.join(os.path.dirname(__file__), TEMPLATE_FILE)
    with open(template_path) as f:
        template = Template(f.read())

    rendered = template.render(
        domain=domain,
        ssl_cert=ssl_cert,
        ssl_key=ssl_key,
        upstream_host=upstream_host,
        upstream_port=upstream_port,
    )

    with open(output_file, "w") as f:
        f.write(rendered)

    print(f"Generated {output_file}")


if __name__ == "__main__":
    # Production config

    domain = "portal.tailccb591.ts.net"
    generate_config(
        domain=domain,
        ssl_cert=f"{domain}.crt",
        ssl_key=f"{domain}.key",
        output_file="nginx.prod.conf"
    )

    # Development config
    domain = "localhost.net"
    generate_config(
        domain=domain,
        ssl_cert=f"{domain}.crt",
        ssl_key=f"{domain}.key",
        output_file="nginx.dev.conf"
    )
