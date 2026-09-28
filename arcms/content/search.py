"""البحث العربي فوق فهرس قاعدة البيانات.

التحليل اللغوي (تطبيع، همزات، تجذيع) يجري في بايثون عبر ‎arcms.arabic‎،
وقاعدة البيانات تتولى الفهرسة والترتيب فقط:
  - PostgreSQL: عمود tsvector بإعداد 'simple' وفهرس GIN وترتيب ts_rank_cd.
  - SQLite: جدول FTS5 وترتيب bm25.
هكذا يتطابق سلوك البحث في البيئتين، ولا نعتمد على قواميس لغوية غائبة.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.db import connection, transaction
from django.utils import timezone
from django.utils.html import strip_tags

from arcms.arabic.analyzer import ParsedQuery, index_text, parse_query

from .models import Article, SearchDocument, Status

FTS_TABLE = "content_search_fts"


def _build_terms(article: Article) -> tuple[str, str, str]:
    tags = " ".join(article.tags.values_list("name", flat=True)) if article.pk else ""
    authors = " ".join(article.authors.values_list("name", flat=True)) if article.pk else ""
    title = index_text(f"{article.title} {article.kicker}")
    lead = index_text(f"{article.subtitle} {article.excerpt} {tags} {authors} {article.dateline}")
    body = index_text(strip_tags(article.body or ""))
    return title, lead, body


def index_article(article: Article) -> None:
    title, lead, body = _build_terms(article)
    with transaction.atomic():
        SearchDocument.objects.update_or_create(
            article=article, defaults={"title_terms": title, "lead_terms": lead, "body_terms": body}
        )
        backend().write(article.pk, title, lead, body)


def remove_article(article_id: int) -> None:
    backend().delete(article_id)


@dataclass
class Hit:
    article_id: int
    score: float


class _Backend:
    def write(self, article_id: int, title: str, lead: str, body: str) -> None:
        raise NotImplementedError

    def delete(self, article_id: int) -> None:
        raise NotImplementedError

    def search(self, q: ParsedQuery, ids_sql: str, ids_params: list, limit: int, offset: int) -> tuple[list[Hit], int]:
        raise NotImplementedError


def _quote_term(term: str) -> str:
    # المفاتيح لا تحوي إلا حروفاً وأرقاماً (من المحلّل)، والاقتباس احتياط إضافي.
    return term.replace("'", "").replace('"', "").replace("\\", "")


class PostgresBackend(_Backend):
    def write(self, article_id, title, lead, body):
        with connection.cursor() as cur:
            cur.execute(
                """
                UPDATE content_searchdocument SET vector =
                    setweight(to_tsvector('simple', %s), 'A') ||
                    setweight(to_tsvector('simple', %s), 'B') ||
                    setweight(to_tsvector('simple', %s), 'C')
                WHERE article_id = %s
                """,
                [title, lead, body, article_id],
            )

    def delete(self, article_id):
        pass  # يُحذف مع السجل (CASCADE)

    @staticmethod
    def tsquery(q: ParsedQuery) -> str:
        groups = []
        for g in q.groups:
            alts = " | ".join(f"'{_quote_term(t)}'" for t in sorted(g) if _quote_term(t))
            if alts:
                groups.append(f"({alts})")
        return " & ".join(groups)

    def search(self, q, ids_sql, ids_params, limit, offset):
        tsq = self.tsquery(q)
        if not tsq:
            return [], 0
        base = f"""
            FROM content_searchdocument d
            JOIN content_article a ON a.id = d.article_id
            WHERE d.vector @@ to_tsquery('simple', %s) AND a.id IN ({ids_sql})
        """
        with connection.cursor() as cur:
            cur.execute("SELECT count(*) " + base, [tsq, *ids_params])
            total = cur.fetchone()[0]
            cur.execute(
                f"""
                SELECT a.id,
                  ts_rank_cd(d.vector, to_tsquery('simple', %s), 1) *
                  (1.0 + 1.0 / (1.0 + GREATEST(0, EXTRACT(EPOCH FROM (now() - COALESCE(a.published_at, a.created_at))) / 604800.0)))
                  AS score
                {base}
                ORDER BY score DESC, a.published_at DESC NULLS LAST
                LIMIT %s OFFSET %s
                """,
                [tsq, tsq, *ids_params, limit, offset],
            )
            return [Hit(r[0], float(r[1])) for r in cur.fetchall()], total


class SqliteBackend(_Backend):
    def write(self, article_id, title, lead, body):
        with connection.cursor() as cur:
            cur.execute(f"DELETE FROM {FTS_TABLE} WHERE rowid = %s", [article_id])
            cur.execute(
                f"INSERT INTO {FTS_TABLE}(rowid, title, lead, body) VALUES (%s, %s, %s, %s)",
                [article_id, title, lead, body],
            )

    def delete(self, article_id):
        with connection.cursor() as cur:
            cur.execute(f"DELETE FROM {FTS_TABLE} WHERE rowid = %s", [article_id])

    @staticmethod
    def match(q: ParsedQuery) -> str:
        groups = []
        for g in q.groups:
            alts = " OR ".join(f'"{_quote_term(t)}"' for t in sorted(g) if _quote_term(t))
            if alts:
                groups.append(f"({alts})")
        return " AND ".join(groups)

    def search(self, q, ids_sql, ids_params, limit, offset):
        match = self.match(q)
        if not match:
            return [], 0
        base = f"""
            FROM {FTS_TABLE} f JOIN content_article a ON a.id = f.rowid
            WHERE {FTS_TABLE} MATCH %s AND a.id IN ({ids_sql})
        """
        with connection.cursor() as cur:
            cur.execute("SELECT count(*) " + base, [match, *ids_params])
            total = cur.fetchone()[0]
            # bm25 سالب: الأصغر أفضل. نضيف تفضيلاً خفيفاً للأحدث.
            cur.execute(
                f"""
                SELECT a.id, bm25({FTS_TABLE}, 10.0, 4.0, 1.0) AS rank,
                       julianday('now') - julianday(COALESCE(a.published_at, a.created_at)) AS age
                {base}
                ORDER BY (rank / (1.0 + 1.0 / (1.0 + MAX(0, age) / 7.0))) ASC, a.published_at DESC
                LIMIT %s OFFSET %s
                """,
                [match, *ids_params, limit, offset],
            )
            return [Hit(r[0], -float(r[1])) for r in cur.fetchall()], total


def backend() -> _Backend:
    return PostgresBackend() if connection.vendor == "postgresql" else SqliteBackend()


@dataclass
class SearchResult:
    query: ParsedQuery
    articles: list[Article]
    total: int


def search_articles(
    raw: str,
    *,
    queryset=None,
    public: bool = True,
    limit: int = 20,
    offset: int = 0,
    since: datetime | None = None,
    until: datetime | None = None,
) -> SearchResult:
    """يبحث ويعيد المواد مرتبة بالصلة. queryset يقيّد النطاق (قسم، نوع، كاتب...)."""
    q = parse_query(raw)
    if q.is_empty:
        return SearchResult(q, [], 0)
    qs = queryset if queryset is not None else Article.objects.all()
    if public:
        # المؤجَّل (تاريخ نشر في المستقبل) لا يظهر في البحث كما لا يظهر في صفحته.
        qs = qs.filter(status=Status.PUBLISHED, published_at__lte=timezone.now())
    if since:
        qs = qs.filter(published_at__gte=since)
    if until:
        qs = qs.filter(published_at__lt=until)
    ids_sql, ids_params = qs.order_by().values("id").query.sql_with_params()
    hits, total = backend().search(q, ids_sql, list(ids_params), limit, offset)
    by_id = {
        a.pk: a
        for a in Article.objects.filter(pk__in=[h.article_id for h in hits])
        .select_related("category", "featured_image")
        .prefetch_related("authors")
    }
    articles = [by_id[h.article_id] for h in hits if h.article_id in by_id]
    if q.phrases:
        from arcms.arabic.normalize import normalize

        def has_phrases(a: Article) -> bool:
            text = normalize(f"{a.title} {a.subtitle} {a.excerpt} {strip_tags(a.body or '')}")
            return all(p in text for p in q.phrases)

        articles = [a for a in articles if has_phrases(a)]
    return SearchResult(q, articles, total)


def rebuild_index(batch: int = 500, stdout=None) -> int:
    count = 0
    qs = Article.objects.order_by("id").prefetch_related("tags", "authors")
    for article in qs.iterator(chunk_size=batch):
        index_article(article)
        count += 1
        if stdout and count % 1000 == 0:
            stdout.write(f"… {count}")
    return count
