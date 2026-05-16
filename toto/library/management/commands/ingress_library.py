from django.utils.text import slugify

from toto.ingress import IngressCommand
from toto.people.models import Person
from toto.palimpsest.models import Tag

from toto.library.models import Article, Book


class Command(IngressCommand):
    help = "Creates demo library data (books and articles)"

    def process(self):
        if not self.full:
            return

        # ----------------------------------------------------
        # Tags
        # ----------------------------------------------------
        tag_names = [
            "python", "django", "software-architecture",
            "machine-learning", "databases", "devops",
        ]

        tags = {}
        for name in tag_names:
            tag, _ = Tag.objects.get_or_create(
                slug=slugify(name),
                defaults={"name": name},
            )
            tags[name] = tag

        # ----------------------------------------------------
        # Authors (use existing persons if available)
        # ----------------------------------------------------
        persons = list(Person.objects.all()[:3])

        # ----------------------------------------------------
        # Books
        # ----------------------------------------------------
        book_data = [
            {
                "title": "Two Scoops of Django",
                "year": 2022,
                "publisher": "Two Scoops Press",
                "edition": "3rd",
                "isbn": "978-0-9916376-5-0",
                "abstract": "Best practices for Django web framework development, covering project layout, testing, security, and deployment patterns.",
                "tags": ["django", "python"],
            },
            {
                "title": "Clean Code",
                "year": 2008,
                "publisher": "Prentice Hall",
                "edition": "1st",
                "isbn": "978-0-13-235088-4",
                "abstract": "A handbook of agile software craftsmanship with practical guidance on writing readable, maintainable code.",
                "tags": ["software-architecture"],
            },
            {
                "title": "Designing Data-Intensive Applications",
                "year": 2017,
                "publisher": "O'Reilly Media",
                "edition": "1st",
                "isbn": "978-1-4919-0310-7",
                "abstract": "The big ideas behind reliable, scalable, and maintainable systems, covering data models, storage engines, and distributed systems.",
                "tags": ["databases", "software-architecture"],
            },
            {
                "title": "Fluent Python",
                "year": 2022,
                "publisher": "O'Reilly Media",
                "edition": "2nd",
                "isbn": "978-1-4920-5635-5",
                "abstract": "How the Python language works under the hood, with clear examples of idiomatic patterns and advanced features.",
                "tags": ["python"],
            },
        ]

        for spec in book_data:
            slug = slugify(spec["title"])
            book, _ = Book.objects.get_or_create(
                slug=slug,
                defaults={
                    "title": spec["title"],
                    "year": spec["year"],
                    "publisher": spec["publisher"],
                    "edition": spec["edition"],
                    "isbn": spec["isbn"],
                    "abstract": spec["abstract"],
                },
            )
            book.tags.set([tags[t] for t in spec["tags"]])
            if persons:
                book.authors.set(persons[:1])
            self.stdout.write(f"  book: {book.title}")

        # ----------------------------------------------------
        # Articles
        # ----------------------------------------------------
        article_data = [
            {
                "title": "Attention Is All You Need",
                "year": 2017,
                "journal": "NeurIPS",
                "volume": "30",
                "issue": "",
                "pages": "5998–6008",
                "doi": "10.48550/arXiv.1706.03762",
                "abstract": "The dominant sequence transduction models are based on complex recurrent or convolutional neural networks. This paper proposes the Transformer, a model architecture eschewing recurrence and relying entirely on an attention mechanism.",
                "tags": ["machine-learning"],
            },
            {
                "title": "Django: The Web Framework for Perfectionists with Deadlines",
                "year": 2021,
                "journal": "Software: Practice and Experience",
                "volume": "51",
                "issue": "4",
                "pages": "789–810",
                "doi": "",
                "abstract": "An overview of Django's architecture decisions, ORM design, and ecosystem after fifteen years of production use.",
                "tags": ["django", "python"],
            },
            {
                "title": "MapReduce: Simplified Data Processing on Large Clusters",
                "year": 2004,
                "journal": "OSDI",
                "volume": "6",
                "issue": "",
                "pages": "10–10",
                "doi": "10.1145/1327452.1327492",
                "abstract": "MapReduce is a programming model and implementation for processing and generating large datasets. Programs written in the functional style are automatically parallelized.",
                "tags": ["databases", "devops"],
            },
            {
                "title": "Continuous Delivery: Reliable Software Releases through Build, Test, and Deployment Automation",
                "year": 2010,
                "journal": "IEEE Software",
                "volume": "27",
                "issue": "2",
                "pages": "14–16",
                "doi": "10.1109/MS.2010.61",
                "abstract": "Continuous delivery is a set of practices and principles aimed at building, testing, and releasing software with greater speed and frequency.",
                "tags": ["devops", "software-architecture"],
            },
        ]

        for spec in article_data:
            slug = slugify(spec["title"])
            article, _ = Article.objects.get_or_create(
                slug=slug,
                defaults={
                    "title": spec["title"],
                    "year": spec["year"],
                    "journal": spec["journal"],
                    "volume": spec["volume"],
                    "issue": spec["issue"],
                    "pages": spec["pages"],
                    "doi": spec["doi"],
                    "abstract": spec["abstract"],
                },
            )
            article.tags.set([tags[t] for t in spec["tags"]])
            if persons:
                article.authors.set(persons[:2])
            self.stdout.write(f"  article: {article.title}")

        self.stdout.write(self.style.SUCCESS("[Ingress] Library demo data created successfully."))
