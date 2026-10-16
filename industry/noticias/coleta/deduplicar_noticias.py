from sqlalchemy import text

from industry.shared.connections import NOTICIAS_PG, get_sqlalchemy_engine


def deduplicar_noticias(**kwargs):
    engine = get_sqlalchemy_engine(NOTICIAS_PG)
    with engine.connect() as conn:
        conn.execute(
            text("""
            DELETE FROM noticias.noticias
            WHERE ctid NOT IN (
                SELECT DISTINCT ON (titulo) ctid
                FROM noticias.noticias
                ORDER BY titulo, ctid
            )
        """)
        )
        conn.commit()
