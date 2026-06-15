-- ============================================================
-- 052_multibagger_selection_rationale.sql
-- The emerging dossier must always explain WHY a name was shortlisted -- the
-- theory grounded in the 6 DNA trait scores (which always exist, even when
-- external coverage is thin). deep_research.py now writes this section.
-- ============================================================

ALTER TABLE public.multibagger_theses
  ADD COLUMN IF NOT EXISTS selection_rationale text;

COMMENT ON COLUMN public.multibagger_theses.selection_rationale IS
  'Why the screen shortlisted this name -- the compounding theory grounded in the DNA trait scores. Always written (trait scores always exist), even when external data is thin. The spine of the emerging dossier.';
