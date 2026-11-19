import shutil
from typing import Any, Dict, List, Optional, Tuple

import sqlmodel as sq

from ..context import ctx
from ..dao import (
    Artifact,
    ChapterTranslation,
    GlossaryCandidate,
    GlossaryEntry,
    GlossaryTerm,
    LanguageCode,
    Novel,
    NovelSort,
    NovelTag,
    NovelTranslation,
    TermTier,
    VolumeTranslation,
)
from ..exceptions import ServerErrors
from ..server.models import Paginated
from ..utils.text_tools import normalize_term_key
from .glossary import (
    bump_revision,
    create_manual_entry,
    decide_manually,
    get_or_create_term,
    glossary_lock,
    glossary_scan_status,
    import_glossary_terms,
    reject_term,
    scan_novel_terms,
    term_lock,
)


class NovelService:
    def list(
        self,
        search: str = "",
        offset: int = 0,
        limit: int = 20,
        domain: str = "",
        language: Optional[str] = None,
        tags: Optional[List[str]] = None,
        manga: Optional[bool] = None,
        mtl: Optional[bool] = None,
        min_chapters: int = 0,
        sort: NovelSort = NovelSort.updated,
    ) -> Paginated[Novel]:
        with ctx.db.session() as sess:
            stmt = sq.select(Novel)
            cnt = sq.select(sq.func.count()).select_from(Novel)

            # Apply filters
            conditions: List[Any] = []

            if domain:
                conditions.append(sq.col(Novel.url).ilike(f"%{domain}%"))

            if search:
                conditions.append(
                    sq.or_(
                        sq.col(Novel.title).ilike(f"%{search}%"),
                        sq.col(Novel.authors).ilike(f"%{search}%"),
                        sq.col(Novel.synopsis).ilike(f"%{search}%"),
                    )
                )

            if language:
                conditions.append(sq.col(Novel.language) == language)

            if manga is not None:
                conditions.append(sq.col(Novel.manga).is_(manga))
            if mtl is not None:
                conditions.append(sq.col(Novel.mtl).is_(mtl))

            if min_chapters > 0:
                conditions.append(sq.col(Novel.chapter_count) >= min_chapters)

            if tags:
                # Match all selected tags via the indexed association table.
                wanted = sorted(set(tags))
                tag_match = (
                    sq.select(NovelTag.novel_id)
                    .where(sq.col(NovelTag.tag_name).in_(wanted))
                    .group_by(NovelTag.novel_id)
                    .having(sq.func.count(sq.col(NovelTag.tag_name)) == len(wanted))
                )
                conditions.append(sq.col(Novel.id).in_(tag_match))

            if conditions:
                cnd = sq.and_(*conditions)
                stmt = stmt.where(cnd)
                cnt = cnt.where(cnd)

            # Apply sorting
            if sort == NovelSort.popular:
                stmt = stmt.order_by(
                    sq.desc(Novel.popularity),
                    sq.desc(Novel.updated_at),
                )
            elif sort == NovelSort.created:
                stmt = stmt.order_by(sq.desc(Novel.created_at))
            elif sort == NovelSort.chapters:
                stmt = stmt.order_by(sq.desc(Novel.chapter_count))
            elif sort == NovelSort.title_asc:
                stmt = stmt.order_by(sq.asc(Novel.title))
            elif sort == NovelSort.title_desc:
                stmt = stmt.order_by(sq.desc(Novel.title))
            else:
                stmt = stmt.order_by(sq.desc(Novel.updated_at))

            # Apply pagination
            stmt = stmt.offset(offset).limit(limit)

            total = sess.exec(cnt).one()
            items = sess.exec(stmt).all()

            return Paginated(
                total=total,
                offset=offset,
                limit=limit,
                items=list(items),
            )

    def list_tags(self) -> Dict[str, int]:
        with ctx.db.session() as sess:
            rows = sess.exec(
                sq.select(NovelTag.tag_name, sq.func.count())
                .group_by(NovelTag.tag_name)
                .order_by(
                    sq.desc(sq.func.count()),
                    sq.asc(NovelTag.tag_name),
                )
            ).all()
        return {name: count for name, count in rows}

    def list_domains(self) -> Dict[str, int]:
        with ctx.db.session() as sess:
            domains = sess.exec(
                sq.select(
                    Novel.domain,
                    sq.func.count(sq.col(Novel.id)).label("total_novels"),
                ).group_by(Novel.domain)
            ).all()
        return {domain: total_novels for domain, total_novels in domains}

    def get(self, novel_id: str, language: Optional[LanguageCode] = None) -> Novel:
        with ctx.db.session() as sess:
            novel = sess.get(Novel, novel_id)
            if not novel:
                raise ServerErrors.no_such_novel
        if language:
            translation = self.get_novel_translation(novel, language)
            if not translation:
                raise ServerErrors.no_such_novel.with_extra(language)
            novel.title = translation.title
            novel.authors = translation.authors
            novel.synopsis = translation.synopsis
        return novel

    def list_translation_languages(self, novel_id: str) -> List[LanguageCode]:
        with ctx.db.session() as sess:
            translations = sess.exec(
                sq.select(NovelTranslation.language).where(
                    NovelTranslation.novel_id == novel_id,
                )
            ).all()
            return [LanguageCode(lang) for lang in translations]

    def get_novel_translation(self, novel: Novel, language: LanguageCode):
        with ctx.db.session() as sess:
            return sess.exec(
                sq.select(NovelTranslation)
                .where(
                    NovelTranslation.novel_id == novel.id,
                    NovelTranslation.language == language,
                )
                .limit(1)
            ).first()

    def list_glossaries(
        self,
        novel_id: str,
        language: Optional[LanguageCode] = None,
    ) -> Dict[str, Dict[str, str]]:
        """Decided terms for a novel, keyed by language then source term."""
        with ctx.db.session() as sess:
            stmt = (
                sq.select(GlossaryTerm.source, GlossaryEntry.language, GlossaryEntry.rendering)
                .join(GlossaryEntry, sq.col(GlossaryEntry.term_id) == sq.col(GlossaryTerm.id))
                .where(
                    sq.col(GlossaryTerm.novel_id) == novel_id,
                    sq.col(GlossaryEntry.rendering).is_not(None),
                )
            )
            if language:
                stmt = stmt.where(sq.col(GlossaryEntry.language) == language.value)
            rows = sess.exec(stmt).all()
        glossaries: Dict[str, Dict[str, str]] = {}
        for source, lang, rendering in rows:
            if rendering:
                glossaries.setdefault(lang, {})[source] = rendering
        return glossaries

    def list_glossary_extractors(self, novel_id: str) -> List[Dict[str, Any]]:
        """The extraction methods available for this novel's script."""
        from .glossary_extract import describe_extractors

        with ctx.db.session() as sess:
            novel = sess.get(Novel, novel_id)
        if novel is None:
            raise ServerErrors.no_such_novel
        return describe_extractors(novel.language or None)

    def list_glossary_terms(
        self,
        novel_id: str,
        language: str,
        *,
        offset: int = 0,
        limit: int = 50,
        search: str = "",
        tier: str = "all",
        include_fragments: bool = False,
        min_freq: int = 1,
        categorized: bool = False,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """One page of a novel's terms with their decision in `language`.

        This is what the review screen lists: source and metadata always, the
        rendering and lock when one exists, and the engine's alternatives as
        candidate renderings. Fragments — surfaces a longer kept term already
        accounts for — are hidden unless asked for, and a `tier` filter is what
        keeps the long tail out of the way. An unbacked term (no occurrence is
        left, so no scan proposes it any more) is hidden until the next scan
        finds it again; imported terms are never hidden that way.
        """
        with ctx.db.session() as sess:
            conditions = [sq.col(GlossaryTerm.novel_id) == novel_id]
            needle = search.strip()
            if needle:
                conditions.append(sq.col(GlossaryTerm.source).ilike(f"%{needle}%"))
            if tier in {
                TermTier.core.value,
                TermTier.tail.value,
            }:
                conditions.append(sq.col(GlossaryTerm.tier) == TermTier(tier))
            if not include_fragments:
                conditions.append(sq.col(GlossaryTerm.fragment_of).is_(None))
            if min_freq > 0:
                conditions.append(
                    sq.or_(
                        sq.col(GlossaryTerm.freq) >= min_freq,
                        sq.col(GlossaryTerm.origin) == "seed",
                    )
                )
            if categorized:
                conditions.append(sq.col(GlossaryTerm.category).is_not(None))

            total = sess.exec(
                sq.select(sq.func.count()).select_from(GlossaryTerm).where(*conditions)
            ).one()
            rows = sess.exec(
                sq.select(GlossaryTerm, GlossaryEntry)
                .join(
                    GlossaryEntry,
                    (sq.col(GlossaryEntry.term_id) == sq.col(GlossaryTerm.id))
                    & (sq.col(GlossaryEntry.language) == language),
                    isouter=True,
                )
                .where(*conditions)
                .order_by(sq.col(GlossaryTerm.freq).desc(), sq.col(GlossaryTerm.source))
                .offset(offset)
                .limit(limit)
            ).all()

            term_ids = [term.id for term, _ in rows]
            candidates: Dict[str, List[str]] = {}
            if term_ids:
                for term_id, rendering in sess.exec(
                    sq.select(GlossaryCandidate.term_id, GlossaryCandidate.rendering)
                    .where(
                        sq.col(GlossaryCandidate.term_id).in_(term_ids),
                        sq.col(GlossaryCandidate.language) == language,
                    )
                    .order_by(
                        sq.col(GlossaryCandidate.chosen).desc(),
                        sq.col(GlossaryCandidate.votes).desc(),
                    )
                ).all():
                    candidates.setdefault(term_id, []).append(rendering)

            parents: Dict[str, str] = {}
            parent_ids = {term.fragment_of for term, _ in rows if term.fragment_of}
            if parent_ids:
                for parent_id, source in sess.exec(
                    sq.select(GlossaryTerm.id, GlossaryTerm.source).where(
                        sq.col(GlossaryTerm.id).in_(list(parent_ids))
                    )
                ).all():
                    parents[str(parent_id)] = source

        items = [
            {
                "id": term.id,
                "source": term.source,
                "pinyin": term.pinyin,
                "category": term.category,
                # SQLite's additive reconcile adds new columns NULLable, so a
                # row written by an older build can carry NULL here.
                "tier": term.tier or TermTier.core,
                "origin": term.origin or "frequency",
                "fragment_of": parents.get(term.fragment_of or "") or None,
                "freq": term.freq,
                "first_chapter_serial": term.first_chapter_serial,
                "rendering": entry.rendering if entry else None,
                "locked": bool(entry and entry.locked),
                "candidates": candidates.get(term.id, []),
            }
            for term, entry in rows
        ]
        return items, int(total)

    def extract_glossary_terms(
        self,
        novel_id: str,
        *,
        extractors: Optional[List[str]] = None,
        limit: int = 50,
        min_freq: Optional[int] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        """Scan stored chapters for terms; returns the scan report."""
        return scan_novel_terms(
            novel_id,
            extractors=extractors,
            limit=limit,
            min_freq=min_freq,
            force=force,
        )

    def glossary_scan_status(
        self,
        novel_id: str,
        extractors: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """How much of the novel the chosen methods have covered."""
        return glossary_scan_status(novel_id, extractors=extractors)

    def import_glossary_terms(self, novel_id: str, terms: List[str]) -> Dict[str, int]:
        """Store a pasted term list without reading a chapter."""
        return import_glossary_terms(novel_id, terms)

    def update_glossary_term(
        self,
        novel_id: str,
        term_id: str,
        language: LanguageCode,
        rendering: Optional[str],
        *,
        user_id: Optional[str] = None,
    ) -> bool:
        """Decide one term in one language; an empty rendering undecides it.

        Same invariants as `update_glossary`, just for a single row: the write is
        locked, versioned and logged.
        """
        lang = language.value
        value = (rendering or "").strip() or None
        with glossary_lock(novel_id, lang), ctx.db.session() as sess:
            term = sess.get(GlossaryTerm, term_id)
            if term is None or term.novel_id != novel_id:
                raise ServerErrors.invalid_input.with_extra("Unknown glossary term")
            entry = sess.exec(
                sq.select(GlossaryEntry).where(
                    sq.col(GlossaryEntry.term_id) == term_id,
                    sq.col(GlossaryEntry.language) == lang,
                )
            ).first()
            if value is None:
                if entry is None or (entry.rendering is None and not entry.locked):
                    return False
                decide_manually(sess, entry, None, user_id=user_id)
            elif entry is None:
                create_manual_entry(sess, term.id, lang, value, user_id=user_id)
            elif (entry.rendering, entry.locked) != (value, True):
                decide_manually(sess, entry, value, user_id=user_id)
            else:
                return False
            bump_revision(sess, novel_id, lang)
            sess.commit()
            return True

    def reject_glossary_term(
        self,
        novel_id: str,
        term_id: str,
        reason: Optional[str] = None,
    ) -> bool:
        """Delete a term and remember its surface as a false positive."""
        with term_lock(novel_id), ctx.db.session() as sess:
            if not reject_term(sess, novel_id, term_id, reason):
                return False
            sess.commit()
            return True

    def update_glossary(
        self,
        novel_id: str,
        language: LanguageCode,
        terms: Dict[str, str],
        *,
        user_id: Optional[str] = None,
    ) -> Dict[str, str]:
        """Replace one language's glossary with editor decisions.

        Entries written here are locked, so an engine merge can never overwrite
        them. Repeating an engine's current rendering counts as adopting it, so
        that save locks the entry too. A term dropped from `terms` becomes
        undecided again rather than being deleted, keeping its identity and
        history.
        """
        lang = language.value
        incoming: Dict[str, str] = {}
        sources: Dict[str, str] = {}
        for key, value in terms.items():
            source = str(key or "").strip()
            rendering = str(value or "").strip()
            if source and rendering:
                source_key = normalize_term_key(source)
                incoming[source_key] = rendering
                sources[source_key] = source

        with glossary_lock(novel_id, lang), ctx.db.session() as sess:
            if not sess.get(Novel, novel_id):
                raise ServerErrors.no_such_novel
            pending = dict(incoming)
            changed = 0

            existing = sess.exec(
                sq.select(GlossaryTerm, GlossaryEntry)
                .join(GlossaryEntry, sq.col(GlossaryEntry.term_id) == sq.col(GlossaryTerm.id))
                .where(
                    sq.col(GlossaryTerm.novel_id) == novel_id,
                    sq.col(GlossaryEntry.language) == lang,
                )
            ).all()

            for term, entry in existing:
                rendering = pending.pop(term.source_key, None)
                if rendering is None:
                    if entry.rendering is None:
                        continue
                    decide_manually(sess, entry, None, user_id=user_id)
                    changed += 1
                elif (entry.rendering, entry.locked) != (rendering, True):
                    decide_manually(sess, entry, rendering, user_id=user_id)
                    changed += 1

            for source_key, rendering in pending.items():
                term = get_or_create_term(sess, novel_id, sources[source_key], origin="manual")
                create_manual_entry(sess, term.id, lang, rendering, user_id=user_id)
                changed += 1

            if changed:
                bump_revision(sess, novel_id, lang)
            sess.commit()

        return {sources[key]: rendering for key, rendering in incoming.items()}

    def delete(self, novel_id: str, language: Optional[LanguageCode] = None) -> bool:
        if language:
            return self.delete_translation(novel_id, language)
        novel_dir = ctx.files.resolve(f"novels/{novel_id}")
        shutil.rmtree(novel_dir, True)
        with ctx.db.session() as sess:
            novel = sess.get(Novel, novel_id)
            if not novel:
                return True
            sess.exec(
                sq.delete(NovelTag).where(
                    sq.col(NovelTag.novel_id) == novel_id,
                )
            )
            sess.delete(novel)
            sess.commit()
        ctx.recommendations.invalidate(novel_id)
        ctx.recommendations.index_remove(novel_id)
        return True

    def delete_translation(self, novel_id: str, language: LanguageCode) -> bool:
        """Remove one target language of a novel: translation rows, translated
        chapter files, and artifacts in that language.

        The glossary is kept, so a later translation of the same language reuses
        the editor's decisions instead of starting over.
        """
        lang = language.value
        with ctx.db.session() as sess:
            chapters = sess.exec(
                sq.select(ChapterTranslation).where(
                    sq.col(ChapterTranslation.novel_id) == novel_id,
                    sq.col(ChapterTranslation.language) == lang,
                )
            ).all()
            for chapter in chapters:
                ctx.files.resolve(chapter.content_file).unlink(True)

            artifacts = sess.exec(
                sq.select(Artifact).where(
                    sq.col(Artifact.novel_id) == novel_id,
                    sq.col(Artifact.language) == lang,
                )
            ).all()
            for artifact in artifacts:
                ctx.files.resolve(artifact.output_file).unlink(True)
                sess.delete(artifact)

            for model in (ChapterTranslation, VolumeTranslation, NovelTranslation):
                sess.exec(
                    sq.delete(model).where(
                        sq.col(model.novel_id) == novel_id,
                        sq.col(model.language) == lang,
                    )
                )
            sess.commit()
        return True

    def find_by_url(self, novel_url: str) -> Optional[Novel]:
        with ctx.db.session() as sess:
            return sess.exec(sq.select(Novel).where(Novel.url == novel_url)).first()
