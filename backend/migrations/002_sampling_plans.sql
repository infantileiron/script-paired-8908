-- 分层随机抽检计划：总体名册、抽样键、发单凭据与回填记录
-- 与 backend/src/models/entities.py 中的 Sampling* 模型对应；ORM 首次启动也会建表。

CREATE TABLE IF NOT EXISTS public.sampling_plans (
    id integer NOT NULL,
    name character varying(100),
    seed integer NOT NULL,
    groups json NOT NULL,
    state character varying(20),
    result json,
    error text,
    created_at character varying(40),
    dispatched_at character varying(40),
    closed_at character varying(40),
    checksum character varying(64)
);

CREATE SEQUENCE IF NOT EXISTS public.sampling_plans_id_seq
    AS integer START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;

ALTER SEQUENCE public.sampling_plans_id_seq OWNED BY public.sampling_plans.id;
ALTER TABLE ONLY public.sampling_plans ALTER COLUMN id SET DEFAULT nextval('public.sampling_plans_id_seq'::regclass);
ALTER TABLE ONLY public.sampling_plans ADD CONSTRAINT sampling_plans_pkey PRIMARY KEY (id);

CREATE TABLE IF NOT EXISTS public.sampling_members (
    id integer NOT NULL,
    plan_id integer NOT NULL,
    team character varying(40) NOT NULL,
    code character varying(60) NOT NULL,
    selection_key character varying(64) NOT NULL,
    rank integer NOT NULL,
    selected boolean,
    inclusion_probability double precision NOT NULL,
    design_weight double precision NOT NULL
);

CREATE SEQUENCE IF NOT EXISTS public.sampling_members_id_seq
    AS integer START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;

ALTER SEQUENCE public.sampling_members_id_seq OWNED BY public.sampling_members.id;
ALTER TABLE ONLY public.sampling_members ALTER COLUMN id SET DEFAULT nextval('public.sampling_members_id_seq'::regclass);
ALTER TABLE ONLY public.sampling_members ADD CONSTRAINT sampling_members_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.sampling_members ADD CONSTRAINT sampling_members_plan_code_key UNIQUE (plan_id, code);
ALTER TABLE ONLY public.sampling_members
    ADD CONSTRAINT sampling_members_plan_id_fkey FOREIGN KEY (plan_id) REFERENCES public.sampling_plans(id);

CREATE TABLE IF NOT EXISTS public.sampling_dispatches (
    id integer NOT NULL,
    plan_id integer NOT NULL,
    member_id integer NOT NULL,
    token character varying(40) NOT NULL,
    created_at character varying(40)
);

CREATE SEQUENCE IF NOT EXISTS public.sampling_dispatches_id_seq
    AS integer START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;

ALTER SEQUENCE public.sampling_dispatches_id_seq OWNED BY public.sampling_dispatches.id;
ALTER TABLE ONLY public.sampling_dispatches ALTER COLUMN id SET DEFAULT nextval('public.sampling_dispatches_id_seq'::regclass);
ALTER TABLE ONLY public.sampling_dispatches ADD CONSTRAINT sampling_dispatches_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.sampling_dispatches ADD CONSTRAINT sampling_dispatches_plan_member_key UNIQUE (plan_id, member_id);
ALTER TABLE ONLY public.sampling_dispatches
    ADD CONSTRAINT sampling_dispatches_plan_id_fkey FOREIGN KEY (plan_id) REFERENCES public.sampling_plans(id);
ALTER TABLE ONLY public.sampling_dispatches
    ADD CONSTRAINT sampling_dispatches_member_id_fkey FOREIGN KEY (member_id) REFERENCES public.sampling_members(id);

CREATE TABLE IF NOT EXISTS public.sampling_observations (
    id integer NOT NULL,
    plan_id integer NOT NULL,
    member_id integer NOT NULL,
    before double precision,
    after double precision,
    updated_at character varying(40)
);

CREATE SEQUENCE IF NOT EXISTS public.sampling_observations_id_seq
    AS integer START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;

ALTER SEQUENCE public.sampling_observations_id_seq OWNED BY public.sampling_observations.id;
ALTER TABLE ONLY public.sampling_observations ALTER COLUMN id SET DEFAULT nextval('public.sampling_observations_id_seq'::regclass);
ALTER TABLE ONLY public.sampling_observations ADD CONSTRAINT sampling_observations_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.sampling_observations ADD CONSTRAINT sampling_observations_plan_member_key UNIQUE (plan_id, member_id);
ALTER TABLE ONLY public.sampling_observations
    ADD CONSTRAINT sampling_observations_plan_id_fkey FOREIGN KEY (plan_id) REFERENCES public.sampling_plans(id);
ALTER TABLE ONLY public.sampling_observations
    ADD CONSTRAINT sampling_observations_member_id_fkey FOREIGN KEY (member_id) REFERENCES public.sampling_members(id);
