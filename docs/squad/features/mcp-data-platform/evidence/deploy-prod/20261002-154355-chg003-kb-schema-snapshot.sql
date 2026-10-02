--
-- PostgreSQL database dump
--

\restrict Q94CQnlawLZH4gAacXv2YGfXmBvKtaOyKPCvT7bduckh6SU2jL1aw9ej6cZWIm1

-- Dumped from database version 16.15 (Debian 16.15-1.pgdg12+2)
-- Dumped by pg_dump version 16.15 (Debian 16.15-1.pgdg12+2)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: kb; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA kb;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: chunks; Type: TABLE; Schema: kb; Owner: -
--

CREATE TABLE kb.chunks (
    id bigint NOT NULL,
    document_id uuid NOT NULL,
    chunk_index integer NOT NULL,
    content text NOT NULL,
    token_count integer,
    heading_path text,
    embedding public.vector(1024) NOT NULL,
    embedding_model text NOT NULL,
    embedded_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: chunks_id_seq; Type: SEQUENCE; Schema: kb; Owner: -
--

CREATE SEQUENCE kb.chunks_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: chunks_id_seq; Type: SEQUENCE OWNED BY; Schema: kb; Owner: -
--

ALTER SEQUENCE kb.chunks_id_seq OWNED BY kb.chunks.id;


--
-- Name: documents; Type: TABLE; Schema: kb; Owner: -
--

CREATE TABLE kb.documents (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    source_type text NOT NULL,
    source_id text NOT NULL,
    source_uri text NOT NULL,
    title text,
    container text,
    author text,
    content_hash text NOT NULL,
    source_updated_at timestamp with time zone,
    ingested_at timestamp with time zone DEFAULT now() NOT NULL,
    deleted_at timestamp with time zone,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    last_seen_run_id uuid,
    last_seen_at timestamp with time zone,
    chunk_config_hash text,
    visibility text DEFAULT 'team'::text NOT NULL,
    CONSTRAINT documents_visibility_ck CHECK ((visibility = ANY (ARRAY['team'::text, 'restricted'::text])))
);


--
-- Name: ingest_failures; Type: TABLE; Schema: kb; Owner: -
--

CREATE TABLE kb.ingest_failures (
    source_type text NOT NULL,
    source_id text NOT NULL,
    first_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    last_attempt_at timestamp with time zone DEFAULT now() NOT NULL,
    attempts integer DEFAULT 1 NOT NULL,
    stage text NOT NULL,
    code text NOT NULL,
    last_error text,
    CONSTRAINT ingest_failures_stage_check CHECK ((stage = ANY (ARRAY['config'::text, 'connect'::text, 'crawl'::text, 'normalize'::text, 'redact'::text, 'chunk'::text, 'embed'::text, 'persist'::text, 'reconcile'::text, 'prune'::text])))
);


--
-- Name: ingest_runs; Type: TABLE; Schema: kb; Owner: -
--

CREATE TABLE kb.ingest_runs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    source_type text NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    status text DEFAULT 'running'::text NOT NULL,
    documents_seen integer DEFAULT 0 NOT NULL,
    documents_upserted integer DEFAULT 0 NOT NULL,
    documents_skipped integer DEFAULT 0 NOT NULL,
    documents_failed integer DEFAULT 0 NOT NULL,
    chunks_written integer DEFAULT 0 NOT NULL,
    error_summary jsonb,
    CONSTRAINT ingest_runs_status_check CHECK ((status = ANY (ARRAY['running'::text, 'success'::text, 'partial'::text, 'failed'::text])))
);


--
-- Name: ingest_source_state; Type: TABLE; Schema: kb; Owner: -
--

CREATE TABLE kb.ingest_source_state (
    source_type text NOT NULL,
    cursor jsonb,
    last_success_at timestamp with time zone,
    last_run_id uuid
);


--
-- Name: schema_migrations; Type: TABLE; Schema: kb; Owner: -
--

CREATE TABLE kb.schema_migrations (
    version text NOT NULL,
    checksum text NOT NULL,
    applied_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: chunks id; Type: DEFAULT; Schema: kb; Owner: -
--

ALTER TABLE ONLY kb.chunks ALTER COLUMN id SET DEFAULT nextval('kb.chunks_id_seq'::regclass);


--
-- Data for Name: chunks; Type: TABLE DATA; Schema: kb; Owner: -
--

COPY kb.chunks (id, document_id, chunk_index, content, token_count, heading_path, embedding, embedding_model, embedded_at) FROM stdin;
1	897fae17-f07e-4799-b45c-af8388980507	0	The payment worker retries failed transactions three times with exponential backoff before moving the message to the dead letter queue payment-retry-dlq.	21	Payment retry policy > Backoff	[0,0,0,0.15249857,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,-0.15249857,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0.30499715,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,-0.15249857,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,-0.15249857,0,0,0,0,0,0,0,-0.15249857,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0.45749572,0,0,0,0,0,0,0,0,0,-0.15249857,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0.45749572,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0.15249857,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0.15249857,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0.15249857,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0.15249857,0,0,0,0,0,0,0.15249857,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,-0.15249857,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,-0.15249857,0,0.15249857,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,-0.15249857,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0.15249857,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0.15249857,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,-0.30499715,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0]	fake/hashed-bow	2026-10-01 11:33:45.416129+00
2	8eb94b10-f701-4c2e-bc0c-8010295a4382	0	When consumer lag on the orders topic exceeds the alert threshold, scale the consumer group and check the broker for under-replicated partitions. Restart the lagging consumer last.	27	Kafka consumer lag runbook	[0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0.12309149,0,0,0,0,0,0,0,0,0,0.12309149,0,0.12309149,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0.12309149,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,-0.12309149,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,-0.12309149,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,-0.12309149,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0.12309149,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0.6154575,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,-0.12309149,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,-0.12309149,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0.12309149,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,-0.12309149,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0.12309149,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,-0.24618298,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,-0.12309149,0.12309149,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0.12309149,0,0,0,0,0,0,-0.12309149,0,0,0,0.49236596,0,0,0,0,0,0,0,-0.12309149,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,-0.12309149,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0.12309149,0,0,0,-0.12309149,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0]	fake/hashed-bow	2026-10-01 11:33:45.425942+00
\.


--
-- Data for Name: documents; Type: TABLE DATA; Schema: kb; Owner: -
--

COPY kb.documents (id, source_type, source_id, source_uri, title, container, author, content_hash, source_updated_at, ingested_at, deleted_at, metadata, last_seen_run_id, last_seen_at, chunk_config_hash, visibility) FROM stdin;
897fae17-f07e-4799-b45c-af8388980507	confluence	1001	https://wiki.example.test/wiki/spaces/PAY/pages/1001/payment-retry	Payment retry policy	PAY	An Nguyen	9d6e5a88c9986630dd9a4c0c4d8932dbac3c30613c1a2963c90d6562f916d8fb	2026-09-28 09:00:00+00	2026-10-01 11:33:45.416129+00	\N	{"version": 3, "redactions": 0}	4c6f8432-76ae-4d72-9e66-7303b55ddc0b	2026-10-01 14:16:52.457921+00	2eaef2e2c8386f7790bfdd375dda80c5a260496c5e39513aaa5d7e8a2c43edeb	team
8eb94b10-f701-4c2e-bc0c-8010295a4382	confluence	1002	https://wiki.example.test/wiki/spaces/PAY/pages/1002/kafka-lag-runbook	Incident runbook: Kafka consumer lag	PAY	Binh Tran	937cba66b1340c11711fbb8f2168934b0076966351c403ed1ea710900b26a081	2026-09-29 14:30:00+00	2026-10-01 11:33:45.425942+00	\N	{"version": 5, "redactions": 0}	4c6f8432-76ae-4d72-9e66-7303b55ddc0b	2026-10-01 14:16:52.460099+00	2eaef2e2c8386f7790bfdd375dda80c5a260496c5e39513aaa5d7e8a2c43edeb	team
\.


--
-- Data for Name: ingest_failures; Type: TABLE DATA; Schema: kb; Owner: -
--

COPY kb.ingest_failures (source_type, source_id, first_seen_at, last_attempt_at, attempts, stage, code, last_error) FROM stdin;
\.


--
-- Data for Name: ingest_runs; Type: TABLE DATA; Schema: kb; Owner: -
--

COPY kb.ingest_runs (id, source_type, started_at, finished_at, status, documents_seen, documents_upserted, documents_skipped, documents_failed, chunks_written, error_summary) FROM stdin;
290c42b3-95bc-4971-a1e4-88bd7ff737e5	confluence	2026-10-01 11:33:45.389614+00	2026-10-01 11:33:45.430194+00	success	2	2	0	0	2	\N
4c6f8432-76ae-4d72-9e66-7303b55ddc0b	confluence	2026-10-01 14:16:52.42962+00	2026-10-01 14:16:52.463323+00	success	2	0	2	0	0	\N
\.


--
-- Data for Name: ingest_source_state; Type: TABLE DATA; Schema: kb; Owner: -
--

COPY kb.ingest_source_state (source_type, cursor, last_success_at, last_run_id) FROM stdin;
confluence	{"watermark": "2026-09-29T14:30:00+00:00"}	2026-10-01 14:16:52.461124+00	4c6f8432-76ae-4d72-9e66-7303b55ddc0b
\.


--
-- Data for Name: schema_migrations; Type: TABLE DATA; Schema: kb; Owner: -
--

COPY kb.schema_migrations (version, checksum, applied_at) FROM stdin;
0001_extensions	596b8000c60344e260fd21dc1b980732e17882b8b3b6d7f763060b4d62dc1c4b	2026-10-01 04:08:59.73363+00
0002_schema_kb	f65dd1d9937e67fc9ca6635bef2aa732c1ef202214d35356376b06f61a472d2a	2026-10-01 04:08:59.752602+00
0003_indexes	23d47d2a970a906735ca0be37c47d363d1615af96065dc24de440a83c09804c4	2026-10-01 04:08:59.762105+00
0004_ingest_state	709d1d732bd2c4a163ce7320333c53d5b354b85c788709eea471ebe7210e29da	2026-10-01 04:08:59.76496+00
0005_roles	f870284f801f92a3767f6f7b068c5e162db07c04710aa44e9a047a80e13e8f2c	2026-10-01 04:08:59.770592+00
0006_review_followup	b3ba79c5b789dd42d18f5783bda9c25de1bccd81e168e93c93c9f18cff5db5eb	2026-10-01 04:08:59.773615+00
\.


--
-- Name: chunks_id_seq; Type: SEQUENCE SET; Schema: kb; Owner: -
--

SELECT pg_catalog.setval('kb.chunks_id_seq', 2, true);


--
-- Name: chunks chunks_document_index_uk; Type: CONSTRAINT; Schema: kb; Owner: -
--

ALTER TABLE ONLY kb.chunks
    ADD CONSTRAINT chunks_document_index_uk UNIQUE (document_id, chunk_index);


--
-- Name: chunks chunks_pkey; Type: CONSTRAINT; Schema: kb; Owner: -
--

ALTER TABLE ONLY kb.chunks
    ADD CONSTRAINT chunks_pkey PRIMARY KEY (id);


--
-- Name: documents documents_pkey; Type: CONSTRAINT; Schema: kb; Owner: -
--

ALTER TABLE ONLY kb.documents
    ADD CONSTRAINT documents_pkey PRIMARY KEY (id);


--
-- Name: documents documents_source_uk; Type: CONSTRAINT; Schema: kb; Owner: -
--

ALTER TABLE ONLY kb.documents
    ADD CONSTRAINT documents_source_uk UNIQUE (source_type, source_id);


--
-- Name: ingest_failures ingest_failures_pkey; Type: CONSTRAINT; Schema: kb; Owner: -
--

ALTER TABLE ONLY kb.ingest_failures
    ADD CONSTRAINT ingest_failures_pkey PRIMARY KEY (source_type, source_id);


--
-- Name: ingest_runs ingest_runs_pkey; Type: CONSTRAINT; Schema: kb; Owner: -
--

ALTER TABLE ONLY kb.ingest_runs
    ADD CONSTRAINT ingest_runs_pkey PRIMARY KEY (id);


--
-- Name: ingest_source_state ingest_source_state_pkey; Type: CONSTRAINT; Schema: kb; Owner: -
--

ALTER TABLE ONLY kb.ingest_source_state
    ADD CONSTRAINT ingest_source_state_pkey PRIMARY KEY (source_type);


--
-- Name: schema_migrations schema_migrations_pkey; Type: CONSTRAINT; Schema: kb; Owner: -
--

ALTER TABLE ONLY kb.schema_migrations
    ADD CONSTRAINT schema_migrations_pkey PRIMARY KEY (version);


--
-- Name: chunks_document_id_idx; Type: INDEX; Schema: kb; Owner: -
--

CREATE INDEX chunks_document_id_idx ON kb.chunks USING btree (document_id);


--
-- Name: chunks_embedding_hnsw; Type: INDEX; Schema: kb; Owner: -
--

CREATE INDEX chunks_embedding_hnsw ON kb.chunks USING hnsw (embedding public.vector_cosine_ops) WITH (m='16', ef_construction='64');


--
-- Name: documents_source_last_seen_idx; Type: INDEX; Schema: kb; Owner: -
--

CREATE INDEX documents_source_last_seen_idx ON kb.documents USING btree (source_type, last_seen_run_id);


--
-- Name: documents_source_updated_idx; Type: INDEX; Schema: kb; Owner: -
--

CREATE INDEX documents_source_updated_idx ON kb.documents USING btree (source_type, source_updated_at);


--
-- Name: ingest_runs_source_started_idx; Type: INDEX; Schema: kb; Owner: -
--

CREATE INDEX ingest_runs_source_started_idx ON kb.ingest_runs USING btree (source_type, started_at DESC);


--
-- Name: chunks chunks_document_id_fkey; Type: FK CONSTRAINT; Schema: kb; Owner: -
--

ALTER TABLE ONLY kb.chunks
    ADD CONSTRAINT chunks_document_id_fkey FOREIGN KEY (document_id) REFERENCES kb.documents(id) ON DELETE CASCADE;


--
-- Name: documents documents_last_seen_run_id_fkey; Type: FK CONSTRAINT; Schema: kb; Owner: -
--

ALTER TABLE ONLY kb.documents
    ADD CONSTRAINT documents_last_seen_run_id_fkey FOREIGN KEY (last_seen_run_id) REFERENCES kb.ingest_runs(id) ON DELETE SET NULL;


--
-- Name: ingest_source_state ingest_source_state_last_run_id_fkey; Type: FK CONSTRAINT; Schema: kb; Owner: -
--

ALTER TABLE ONLY kb.ingest_source_state
    ADD CONSTRAINT ingest_source_state_last_run_id_fkey FOREIGN KEY (last_run_id) REFERENCES kb.ingest_runs(id) ON DELETE SET NULL;


--
-- PostgreSQL database dump complete
--

\unrestrict Q94CQnlawLZH4gAacXv2YGfXmBvKtaOyKPCvT7bduckh6SU2jL1aw9ej6cZWIm1

