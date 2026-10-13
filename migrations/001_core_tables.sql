-- LongWatch core schema (§6).  One time basis: source-relative seconds for
-- every timeline position; every hashed quantity is an integer in milli-units.

CREATE TABLE reviewers (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  display_name text NOT NULL UNIQUE,
  created_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE sources (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  origin text NOT NULL CHECK (origin IN ('upload','inbox','demo')),
  original_name text NOT NULL,
  storage_path text NOT NULL UNIQUE,
  sha256 char(64) NOT NULL UNIQUE,
  size_bytes bigint NOT NULL,
  duration_s real, fps real, width int, height int,
  video_codec text, audio_codec text, has_audio bool NOT NULL DEFAULT false,
  probe jsonb NOT NULL,
  signal_summary jsonb NOT NULL DEFAULT '{}',
  state text NOT NULL CHECK (state IN
    ('registered','unreadable','missing','windowed','proxied','analyzed','scored','ready','failed')),
  failure_reason text,
  created_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id uuid REFERENCES sources(id),
  kind text NOT NULL CHECK (kind IN ('ingest','export','purge','rescore')),
  state text NOT NULL DEFAULT 'queued' CHECK (state IN ('queued','running','succeeded','failed')),
  stage text CHECK (stage IN ('probe','windows','proxies','signals','detect','score',
                              'bundle','purge','rescore')),
  pct int NOT NULL DEFAULT 0,
  detail text, error text, attempts int NOT NULL DEFAULT 0,
  claimed_at timestamptz, beat_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz);

CREATE TABLE windows (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id uuid NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  idx int NOT NULL, start_s real NOT NULL, end_s real NOT NULL,
  boundary_kind text NOT NULL CHECK (boundary_kind IN ('scene','grid','merged','split')),
  scene_score real, created_at timestamptz NOT NULL DEFAULT now(),
  ml_state text CHECK (ml_state IN ('ok','failed')),
  ml_failure_reason text,
  ml_acked_at timestamptz,
  detections_summary jsonb,
  detections jsonb,
  UNIQUE (source_id, idx),
  CHECK (end_s > start_s));
CREATE INDEX windows_source_idx ON windows (source_id);

CREATE TABLE clips (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  window_id uuid NOT NULL REFERENCES windows(id) ON DELETE CASCADE,
  kind text NOT NULL CHECK (kind IN ('review_proxy','export_excerpt','blurred_excerpt')),
  requested_start_ms bigint NOT NULL, actual_start_ms bigint NOT NULL, end_ms bigint NOT NULL,
  path text NOT NULL, sha256 char(64) NOT NULL, size_bytes bigint NOT NULL,
  cut_mode text CHECK (cut_mode IN ('keyframe_snapped','reencoded')),
  ffmpeg_cmd text NOT NULL,
  transform jsonb,
  created_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE signal_blocks (
  source_id uuid NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  idx int NOT NULL, start_s real NOT NULL,
  series jsonb NOT NULL,
  PRIMARY KEY (source_id, idx));

CREATE TABLE signal_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id uuid NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  kind text NOT NULL CHECK (kind IN ('silence_gap','loudness_spike','audio_absent')),
  t_start real, t_end real, value_milli int, meta jsonb);
CREATE INDEX signal_events_source_idx ON signal_events (source_id, kind);

CREATE TABLE presence_intervals (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  window_id uuid NOT NULL REFERENCES windows(id) ON DELETE CASCADE,
  class_name text NOT NULL,
  t_start real NOT NULL, t_end real NOT NULL,
  samples int NOT NULL, conf_max_milli int NOT NULL,
  synthetic bool NOT NULL DEFAULT false,
  CHECK (class_name IN ('person','dog','cat','horse','cow','sheep','bird','truck')));
CREATE INDEX presence_intervals_window_idx ON presence_intervals (window_id);

CREATE TABLE patterns (
  slug text NOT NULL, version int NOT NULL,
  title text NOT NULL, sentence_template text NOT NULL,
  rule jsonb NOT NULL, weight int NOT NULL CHECK (weight BETWEEN 1 AND 100),
  enabled bool NOT NULL DEFAULT true, builtin bool NOT NULL DEFAULT false,
  is_current bool NOT NULL DEFAULT false,
  updated_at timestamptz NOT NULL DEFAULT now(), updated_by text NOT NULL,
  PRIMARY KEY (slug, version));
CREATE UNIQUE INDEX patterns_one_current ON patterns (slug) WHERE is_current;

CREATE TABLE candidates (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  window_id uuid NOT NULL REFERENCES windows(id) ON DELETE CASCADE,
  pattern_slug text NOT NULL, pattern_version int NOT NULL,
  score int NOT NULL CHECK (score BETWEEN 1 AND 100),
  sentence text NOT NULL CHECK (length(sentence) > 0),
  evidence jsonb NOT NULL,
  state text NOT NULL DEFAULT 'open' CHECK (state IN ('open','accepted','rejected','flagged')),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (window_id, pattern_slug, pattern_version),
  FOREIGN KEY (pattern_slug, pattern_version) REFERENCES patterns(slug, version));
CREATE INDEX candidates_queue_idx ON candidates (state, score DESC);
CREATE INDEX candidates_window_idx ON candidates (window_id);
CREATE INDEX candidates_slug_idx ON candidates (pattern_slug, pattern_version);

CREATE TABLE decisions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  candidate_id uuid NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
  action text NOT NULL CHECK (action IN ('accept','reject','flag')),
  note text, reviewer text NOT NULL,
  src_viewed bool NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX decisions_candidate_idx ON decisions (candidate_id);

CREATE TABLE exports (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  reviewer text NOT NULL, top_n int NOT NULL,
  states jsonb NOT NULL,
  blur_faces bool NOT NULL DEFAULT false,
  cut_precision text NOT NULL DEFAULT 'keyframe' CHECK (cut_precision IN ('keyframe','exact')),
  bundle_path text,
  manifest_sha256 char(64),
  signature text, public_key text,
  created_at timestamptz NOT NULL DEFAULT now(),
  signed_at timestamptz);
