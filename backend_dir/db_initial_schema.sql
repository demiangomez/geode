--
-- PostgreSQL database dump
--

\restrict tGsY5bCIEbTzTAWJwc6PTXrTmC61di1BDkxj3jbadNdsNgE9Gl4yYCM6BMZ5jx6

-- Dumped from database version 15.14 (Debian 15.14-0+deb12u1)
-- Dumped by pg_dump version 15.14 (Debian 15.14-0+deb12u1)

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
-- Name: public; Type: SCHEMA; Schema: -; Owner: postgres
--

-- *not* creating schema, since initdb creates it


ALTER SCHEMA public OWNER TO postgres;

--
-- Name: ecef2neu(numeric, numeric, numeric, numeric, numeric); Type: FUNCTION; Schema: public; Owner: postgres
--

CREATE FUNCTION public.ecef2neu(dx numeric, dy numeric, dz numeric, lat numeric, lon numeric) RETURNS double precision[]
    LANGUAGE sql
    AS $_$
select 
array[-sin(radians($4))*cos(radians($5))*$1 - sin(radians($4))*sin(radians($5))*$2 + cos(radians($4))*$3::numeric,
      -sin(radians($5))*$1 + cos(radians($5))*$2::numeric,
      cos(radians($4))*cos(radians($5))*$1 + cos(radians($4))*sin(radians($5))*$2 + sin(radians($4))*$3::numeric];

$_$;


ALTER FUNCTION public.ecef2neu(dx numeric, dy numeric, dz numeric, lat numeric, lon numeric) OWNER TO postgres;

--
-- Name: fyear(numeric, numeric, numeric, numeric, numeric); Type: FUNCTION; Schema: public; Owner: postgres
--

CREATE FUNCTION public.fyear("Year" numeric, "DOY" numeric, "Hour" numeric DEFAULT 12, "Minute" numeric DEFAULT 0, "Second" numeric DEFAULT 0) RETURNS numeric
    LANGUAGE sql
    AS $_$
SELECT CASE 
WHEN isleapyear(cast($1 as integer)) = True  THEN $1 + ($2 + $3/24 + $4/1440 + $5/86400)/366
WHEN isleapyear(cast($1 as integer)) = False THEN $1 + ($2 + $3/24 + $4/1440 + $5/86400)/365
END;

$_$;


ALTER FUNCTION public.fyear("Year" numeric, "DOY" numeric, "Hour" numeric, "Minute" numeric, "Second" numeric) OWNER TO postgres;

--
-- Name: horizdist(double precision[]); Type: FUNCTION; Schema: public; Owner: postgres
--

CREATE FUNCTION public.horizdist(neu double precision[]) RETURNS double precision
    LANGUAGE sql
    AS $_$

select 
sqrt(($1)[1]^2 + ($1)[2]^2 + ($1)[3]^2)

$_$;


ALTER FUNCTION public.horizdist(neu double precision[]) OWNER TO postgres;

--
-- Name: isleapyear(integer); Type: FUNCTION; Schema: public; Owner: postgres
--

CREATE FUNCTION public.isleapyear(year integer) RETURNS boolean
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
SELECT ($1 % 4 = 0) AND (($1 % 100 <> 0) or ($1 % 400 = 0))
$_$;


ALTER FUNCTION public.isleapyear(year integer) OWNER TO postgres;

--
-- Name: stationalias_check(); Type: FUNCTION; Schema: public; Owner: postgres
--

CREATE FUNCTION public.stationalias_check() RETURNS trigger
    LANGUAGE plpgsql
    AS $$DECLARE
	stnalias BOOLEAN;
BEGIN
SELECT (SELECT "StationCode" FROM stations WHERE "StationCode" = new."StationAlias") IS NULL INTO stnalias;
IF stnalias THEN
    RETURN NEW;
ELSE
	RAISE EXCEPTION 'Invalid station alias: already exists as a station code';
END IF;
END
$$;


ALTER FUNCTION public.stationalias_check() OWNER TO postgres;

--
-- Name: update_station_timespan(character varying, character varying); Type: FUNCTION; Schema: public; Owner: postgres
--

CREATE FUNCTION public.update_station_timespan("NetworkCode" character varying, "StationCode" character varying) RETURNS void
    LANGUAGE sql
    AS $_$
update stations set 
"DateStart" = 
    (SELECT MIN("ObservationFYear") as MINN 
     FROM rinex WHERE "NetworkCode" = $1 AND
     "StationCode" = $2),
"DateEnd" = 
    (SELECT MAX("ObservationFYear") as MAXX 
     FROM rinex WHERE "NetworkCode" = $1 AND
     "StationCode" = $2)
WHERE "NetworkCode" = $1 AND "StationCode" = $2
$_$;


ALTER FUNCTION public.update_station_timespan("NetworkCode" character varying, "StationCode" character varying) OWNER TO postgres;

--
-- Name: update_timespan_trigg(); Type: FUNCTION; Schema: public; Owner: postgres
--

CREATE FUNCTION public.update_timespan_trigg() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    update stations set 
"DateStart" = 
    (SELECT MIN("ObservationFYear") as MINN 
     FROM rinex 
     WHERE "NetworkCode" = new."NetworkCode" AND
           "StationCode" = new."StationCode"),
"DateEnd" = 
    (SELECT MAX("ObservationFYear") as MAXX 
     FROM rinex 
     WHERE "NetworkCode" = new."NetworkCode" AND
           "StationCode" = new."StationCode")
WHERE "NetworkCode" = new."NetworkCode" 
  AND "StationCode" = new."StationCode";

           RETURN new;
END;
$$;


ALTER FUNCTION public.update_timespan_trigg() OWNER TO postgres;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: antennas; Type: TABLE; Schema: public; Owner: gnss_data_osu
--

CREATE TABLE public.antennas (
    "AntennaCode" character varying(22) NOT NULL,
    "AntennaDescription" character varying,
    api_id integer NOT NULL
);


ALTER TABLE public.antennas OWNER TO gnss_data_osu;

--
-- Name: antennas_api_id_seq; Type: SEQUENCE; Schema: public; Owner: gnss_data_osu
--

CREATE SEQUENCE public.antennas_api_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.antennas_api_id_seq OWNER TO gnss_data_osu;

--
-- Name: antennas_api_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: gnss_data_osu
--

ALTER SEQUENCE public.antennas_api_id_seq OWNED BY public.antennas.api_id;


--
-- Name: apr_coords; Type: TABLE; Schema: public; Owner: gnss_data_osu
--

CREATE TABLE public.apr_coords (
    "NetworkCode" character varying NOT NULL,
    "StationCode" character varying NOT NULL,
    "FYear" numeric,
    x numeric,
    y numeric,
    z numeric,
    sn numeric,
    se numeric,
    su numeric,
    "ReferenceFrame" character varying(20),
    "Year" integer NOT NULL,
    "DOY" integer NOT NULL,
    api_id integer NOT NULL
);


ALTER TABLE public.apr_coords OWNER TO gnss_data_osu;

--
-- Name: apr_coords_api_id_seq; Type: SEQUENCE; Schema: public; Owner: gnss_data_osu
--

CREATE SEQUENCE public.apr_coords_api_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.apr_coords_api_id_seq OWNER TO gnss_data_osu;

--
-- Name: apr_coords_api_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: gnss_data_osu
--

ALTER SEQUENCE public.apr_coords_api_id_seq OWNED BY public.apr_coords.api_id;


--
-- Name: aws_sync; Type: TABLE; Schema: public; Owner: gnss_data_osu
--

CREATE TABLE public.aws_sync (
    "NetworkCode" character varying NOT NULL,
    "StationCode" character varying NOT NULL,
    "StationAlias" character varying(4) NOT NULL,
    "Year" numeric NOT NULL,
    "DOY" numeric NOT NULL,
    sync_date timestamp without time zone,
    api_id integer NOT NULL
);


ALTER TABLE public.aws_sync OWNER TO gnss_data_osu;

--
-- Name: aws_sync_api_id_seq; Type: SEQUENCE; Schema: public; Owner: gnss_data_osu
--

CREATE SEQUENCE public.aws_sync_api_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.aws_sync_api_id_seq OWNER TO gnss_data_osu;

--
-- Name: aws_sync_api_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: gnss_data_osu
--

ALTER SEQUENCE public.aws_sync_api_id_seq OWNED BY public.aws_sync.api_id;


--
-- Name: data_source; Type: TABLE; Schema: public; Owner: gnss_data_osu
--

CREATE TABLE public.data_source (
    "NetworkCode" character varying(3) NOT NULL,
    "StationCode" character varying(4) NOT NULL,
    try_order numeric NOT NULL,
    protocol character varying NOT NULL,
    fqdn character varying NOT NULL,
    username character varying,
    password character varying,
    path character varying,
    format character varying,
    api_id integer NOT NULL
);


ALTER TABLE public.data_source OWNER TO gnss_data_osu;

--
-- Name: data_source_api_id_seq; Type: SEQUENCE; Schema: public; Owner: gnss_data_osu
--

CREATE SEQUENCE public.data_source_api_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.data_source_api_id_seq OWNER TO gnss_data_osu;

--
-- Name: data_source_api_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: gnss_data_osu
--

ALTER SEQUENCE public.data_source_api_id_seq OWNED BY public.data_source.api_id;


--
-- Name: earthquakes; Type: TABLE; Schema: public; Owner: gnss_data_osu
--

CREATE TABLE public.earthquakes (
    date timestamp without time zone NOT NULL,
    lat numeric NOT NULL,
    lon numeric NOT NULL,
    depth numeric,
    mag numeric,
    strike1 numeric,
    dip1 numeric,
    rake1 numeric,
    strike2 numeric,
    dip2 numeric,
    rake2 numeric,
    id character varying(40),
    location character varying(120),
    api_id integer NOT NULL
);


ALTER TABLE public.earthquakes OWNER TO gnss_data_osu;

--
-- Name: earthquakes_api_id_seq; Type: SEQUENCE; Schema: public; Owner: gnss_data_osu
--

CREATE SEQUENCE public.earthquakes_api_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.earthquakes_api_id_seq OWNER TO gnss_data_osu;

--
-- Name: earthquakes_api_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: gnss_data_osu
--

ALTER SEQUENCE public.earthquakes_api_id_seq OWNED BY public.earthquakes.api_id;


--
-- Name: etm_params_uid_seq; Type: SEQUENCE; Schema: public; Owner: gnss_data_osu
--

CREATE SEQUENCE public.etm_params_uid_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.etm_params_uid_seq OWNER TO gnss_data_osu;

--
-- Name: etm_params; Type: TABLE; Schema: public; Owner: gnss_data_osu
--

CREATE TABLE public.etm_params (
    "NetworkCode" character varying(3) NOT NULL,
    "StationCode" character varying(4) NOT NULL,
    soln character varying(10) NOT NULL,
    object character varying(10) NOT NULL,
    terms numeric,
    frequencies numeric[],
    jump_type numeric,
    relaxation numeric[],
    "Year" numeric,
    "DOY" numeric,
    action character varying(1),
    uid integer DEFAULT nextval('public.etm_params_uid_seq'::regclass) NOT NULL
);


ALTER TABLE public.etm_params OWNER TO gnss_data_osu;

--
-- Name: etmsv2_uid_seq; Type: SEQUENCE; Schema: public; Owner: gnss_data_osu
--

CREATE SEQUENCE public.etmsv2_uid_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.etmsv2_uid_seq OWNER TO gnss_data_osu;

--
-- Name: etms; Type: TABLE; Schema: public; Owner: gnss_data_osu
--

CREATE TABLE public.etms (
    "NetworkCode" character varying(3) NOT NULL,
    "StationCode" character varying(4) NOT NULL,
    soln character varying(10) NOT NULL,
    object character varying(10) NOT NULL,
    t_ref numeric,
    jump_type numeric,
    relaxation numeric[],
    frequencies numeric[],
    params numeric[],
    sigmas numeric[],
    metadata text,
    hash numeric,
    jump_date timestamp without time zone,
    uid integer DEFAULT nextval('public.etmsv2_uid_seq'::regclass) NOT NULL,
    stack character varying(20)
);


ALTER TABLE public.etms OWNER TO gnss_data_osu;

--
-- Name: events_event_id_seq; Type: SEQUENCE; Schema: public; Owner: gnss_data_osu
--

CREATE SEQUENCE public.events_event_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.events_event_id_seq OWNER TO gnss_data_osu;

--
-- Name: events; Type: TABLE; Schema: public; Owner: gnss_data_osu
--

CREATE TABLE public.events (
    event_id bigint DEFAULT nextval('public.events_event_id_seq'::regclass) NOT NULL,
    "EventDate" timestamp without time zone DEFAULT now() NOT NULL,
    "EventType" character varying(6),
    "NetworkCode" character varying(3),
    "StationCode" character varying(4),
    "Year" integer,
    "DOY" integer,
    "Description" text,
    stack text,
    module text,
    node text
);


ALTER TABLE public.events OWNER TO gnss_data_osu;

--
-- Name: executions_id_seq; Type: SEQUENCE; Schema: public; Owner: gnss_data_osu
--

CREATE SEQUENCE public.executions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.executions_id_seq OWNER TO gnss_data_osu;

--
-- Name: executions; Type: TABLE; Schema: public; Owner: gnss_data_osu
--

CREATE TABLE public.executions (
    id integer DEFAULT nextval('public.executions_id_seq'::regclass) NOT NULL,
    script character varying(40),
    exec_date timestamp without time zone DEFAULT now(),
    api_id integer NOT NULL
);


ALTER TABLE public.executions OWNER TO gnss_data_osu;

--
-- Name: executions_api_id_seq; Type: SEQUENCE; Schema: public; Owner: gnss_data_osu
--

CREATE SEQUENCE public.executions_api_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.executions_api_id_seq OWNER TO gnss_data_osu;

--
-- Name: executions_api_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: gnss_data_osu
--

ALTER SEQUENCE public.executions_api_id_seq OWNED BY public.executions.api_id;


--
-- Name: gamit_htc; Type: TABLE; Schema: public; Owner: gnss_data_osu
--

CREATE TABLE public.gamit_htc (
    "AntennaCode" character varying(22) NOT NULL,
    "HeightCode" character varying(5) NOT NULL,
    v_offset numeric,
    h_offset numeric,
    api_id integer NOT NULL
);


ALTER TABLE public.gamit_htc OWNER TO gnss_data_osu;

--
-- Name: gamit_htc_api_id_seq; Type: SEQUENCE; Schema: public; Owner: gnss_data_osu
--

CREATE SEQUENCE public.gamit_htc_api_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.gamit_htc_api_id_seq OWNER TO gnss_data_osu;

--
-- Name: gamit_htc_api_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: gnss_data_osu
--

ALTER SEQUENCE public.gamit_htc_api_id_seq OWNED BY public.gamit_htc.api_id;


--
-- Name: gamit_soln; Type: TABLE; Schema: public; Owner: gnss_data_osu
--

CREATE TABLE public.gamit_soln (
    "NetworkCode" character varying(3) NOT NULL,
    "StationCode" character varying(4) NOT NULL,
    "Project" character varying(20) NOT NULL,
    "Year" numeric NOT NULL,
    "DOY" numeric NOT NULL,
    "FYear" numeric,
    "X" numeric,
    "Y" numeric,
    "Z" numeric,
    sigmax numeric,
    sigmay numeric,
    sigmaz numeric,
    "VarianceFactor" numeric,
    sigmaxy numeric,
    sigmayz numeric,
    sigmaxz numeric,
    api_id integer NOT NULL
);


ALTER TABLE public.gamit_soln OWNER TO gnss_data_osu;

--
-- Name: gamit_soln_api_id_seq; Type: SEQUENCE; Schema: public; Owner: gnss_data_osu
--

CREATE SEQUENCE public.gamit_soln_api_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.gamit_soln_api_id_seq OWNER TO gnss_data_osu;

--
-- Name: gamit_soln_api_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: gnss_data_osu
--

ALTER SEQUENCE public.gamit_soln_api_id_seq OWNED BY public.gamit_soln.api_id;


--
-- Name: gamit_soln_excl; Type: TABLE; Schema: public; Owner: gnss_data_osu
--

CREATE TABLE public.gamit_soln_excl (
    "NetworkCode" character varying(3) NOT NULL,
    "StationCode" character varying(4) NOT NULL,
    "Project" character varying(20) NOT NULL,
    "Year" bigint NOT NULL,
    "DOY" bigint NOT NULL,
    residual numeric,
    api_id integer NOT NULL
);


ALTER TABLE public.gamit_soln_excl OWNER TO gnss_data_osu;

--
-- Name: gamit_soln_excl_api_id_seq; Type: SEQUENCE; Schema: public; Owner: gnss_data_osu
--

CREATE SEQUENCE public.gamit_soln_excl_api_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.gamit_soln_excl_api_id_seq OWNER TO gnss_data_osu;

--
-- Name: gamit_soln_excl_api_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: gnss_data_osu
--

ALTER SEQUENCE public.gamit_soln_excl_api_id_seq OWNED BY public.gamit_soln_excl.api_id;


--
-- Name: gamit_stats; Type: TABLE; Schema: public; Owner: gnss_data_osu
--

CREATE TABLE public.gamit_stats (
    "Project" character varying(20) NOT NULL,
    subnet numeric NOT NULL,
    "Year" numeric NOT NULL,
    "DOY" numeric NOT NULL,
    "FYear" numeric,
    wl numeric,
    nl numeric,
    nrms numeric,
    relaxed_constrains text,
    max_overconstrained character varying(8),
    updated_apr text,
    iterations numeric,
    node character varying(50),
    execution_time numeric,
    execution_date timestamp without time zone,
    system character(1) NOT NULL,
    api_id integer NOT NULL
);


ALTER TABLE public.gamit_stats OWNER TO gnss_data_osu;

--
-- Name: gamit_stats_api_id_seq; Type: SEQUENCE; Schema: public; Owner: gnss_data_osu
--

CREATE SEQUENCE public.gamit_stats_api_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.gamit_stats_api_id_seq OWNER TO gnss_data_osu;

--
-- Name: gamit_stats_api_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: gnss_data_osu
--

ALTER SEQUENCE public.gamit_stats_api_id_seq OWNED BY public.gamit_stats.api_id;


--
-- Name: gamit_subnets; Type: TABLE; Schema: public; Owner: gnss_data_osu
--

CREATE TABLE public.gamit_subnets (
    "Project" character varying(20) NOT NULL,
    subnet numeric NOT NULL,
    "Year" numeric NOT NULL,
    "DOY" numeric NOT NULL,
    centroid numeric[],
    stations character varying[],
    alias character varying[],
    ties character varying[],
    api_id integer NOT NULL
);


ALTER TABLE public.gamit_subnets OWNER TO gnss_data_osu;

--
-- Name: gamit_subnets_api_id_seq; Type: SEQUENCE; Schema: public; Owner: gnss_data_osu
--

CREATE SEQUENCE public.gamit_subnets_api_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.gamit_subnets_api_id_seq OWNER TO gnss_data_osu;

--
-- Name: gamit_subnets_api_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: gnss_data_osu
--

ALTER SEQUENCE public.gamit_subnets_api_id_seq OWNED BY public.gamit_subnets.api_id;


--
-- Name: gamit_ztd; Type: TABLE; Schema: public; Owner: gnss_data_osu
--

CREATE TABLE public.gamit_ztd (
    "NetworkCode" character varying(3) NOT NULL,
    "StationCode" character varying(4) NOT NULL,
    "Date" timestamp without time zone NOT NULL,
    "Project" character varying(20) NOT NULL,
    "Year" numeric NOT NULL,
    "DOY" numeric NOT NULL,
    "ZTD" numeric NOT NULL,
    model numeric,
    sigma numeric,
    api_id integer NOT NULL
);


ALTER TABLE public.gamit_ztd OWNER TO gnss_data_osu;

--
-- Name: gamit_ztd_api_id_seq; Type: SEQUENCE; Schema: public; Owner: gnss_data_osu
--

CREATE SEQUENCE public.gamit_ztd_api_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.gamit_ztd_api_id_seq OWNER TO gnss_data_osu;

--
-- Name: gamit_ztd_api_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: gnss_data_osu
--

ALTER SEQUENCE public.gamit_ztd_api_id_seq OWNED BY public.gamit_ztd.api_id;


--
-- Name: keys; Type: TABLE; Schema: public; Owner: gnss_data_osu
--

CREATE TABLE public.keys (
    "KeyCode" character varying(7) NOT NULL,
    "TotalChars" integer,
    rinex_col_out character varying,
    rinex_col_in character varying(60),
    isnumeric bit(1),
    api_id integer NOT NULL
);


ALTER TABLE public.keys OWNER TO gnss_data_osu;

--
-- Name: keys_api_id_seq; Type: SEQUENCE; Schema: public; Owner: gnss_data_osu
--

CREATE SEQUENCE public.keys_api_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.keys_api_id_seq OWNER TO gnss_data_osu;

--
-- Name: keys_api_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: gnss_data_osu
--

ALTER SEQUENCE public.keys_api_id_seq OWNED BY public.keys.api_id;


--
-- Name: locks; Type: TABLE; Schema: public; Owner: gnss_data_osu
--

CREATE TABLE public.locks (
    filename text NOT NULL,
    "NetworkCode" character varying(3),
    "StationCode" character varying(4),
    api_id integer NOT NULL
);


ALTER TABLE public.locks OWNER TO gnss_data_osu;

--
-- Name: locks_api_id_seq; Type: SEQUENCE; Schema: public; Owner: gnss_data_osu
--

CREATE SEQUENCE public.locks_api_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.locks_api_id_seq OWNER TO gnss_data_osu;

--
-- Name: locks_api_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: gnss_data_osu
--

ALTER SEQUENCE public.locks_api_id_seq OWNED BY public.locks.api_id;


--
-- Name: networks; Type: TABLE; Schema: public; Owner: gnss_data_osu
--

CREATE TABLE public.networks (
    "NetworkCode" character varying NOT NULL,
    "NetworkName" character varying,
    api_id integer NOT NULL
);


ALTER TABLE public.networks OWNER TO gnss_data_osu;

--
-- Name: networks_api_id_seq; Type: SEQUENCE; Schema: public; Owner: gnss_data_osu
--

CREATE SEQUENCE public.networks_api_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.networks_api_id_seq OWNER TO gnss_data_osu;

--
-- Name: networks_api_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: gnss_data_osu
--

ALTER SEQUENCE public.networks_api_id_seq OWNED BY public.networks.api_id;


--
-- Name: ppp_soln; Type: TABLE; Schema: public; Owner: gnss_data_osu
--

CREATE TABLE public.ppp_soln (
    "NetworkCode" character varying NOT NULL,
    "StationCode" character varying NOT NULL,
    "X" numeric(12,4),
    "Y" numeric(12,4),
    "Z" numeric(12,4),
    "Year" numeric NOT NULL,
    "DOY" numeric NOT NULL,
    "ReferenceFrame" character varying(20) NOT NULL,
    sigmax numeric,
    sigmay numeric,
    sigmaz numeric,
    sigmaxy numeric,
    sigmaxz numeric,
    sigmayz numeric,
    hash integer,
    api_id integer NOT NULL
);


ALTER TABLE public.ppp_soln OWNER TO gnss_data_osu;

--
-- Name: ppp_soln_api_id_seq; Type: SEQUENCE; Schema: public; Owner: gnss_data_osu
--

CREATE SEQUENCE public.ppp_soln_api_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.ppp_soln_api_id_seq OWNER TO gnss_data_osu;

--
-- Name: ppp_soln_api_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: gnss_data_osu
--

ALTER SEQUENCE public.ppp_soln_api_id_seq OWNED BY public.ppp_soln.api_id;


--
-- Name: ppp_soln_excl; Type: TABLE; Schema: public; Owner: gnss_data_osu
--

CREATE TABLE public.ppp_soln_excl (
    "NetworkCode" character varying(3) NOT NULL,
    "StationCode" character varying(4) NOT NULL,
    "Year" numeric NOT NULL,
    "DOY" numeric NOT NULL,
    api_id integer NOT NULL
);


ALTER TABLE public.ppp_soln_excl OWNER TO gnss_data_osu;

--
-- Name: ppp_soln_excl_api_id_seq; Type: SEQUENCE; Schema: public; Owner: gnss_data_osu
--

CREATE SEQUENCE public.ppp_soln_excl_api_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.ppp_soln_excl_api_id_seq OWNER TO gnss_data_osu;

--
-- Name: ppp_soln_excl_api_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: gnss_data_osu
--

ALTER SEQUENCE public.ppp_soln_excl_api_id_seq OWNED BY public.ppp_soln_excl.api_id;


--
-- Name: receivers; Type: TABLE; Schema: public; Owner: gnss_data_osu
--

CREATE TABLE public.receivers (
    "ReceiverCode" character varying(22) NOT NULL,
    "ReceiverDescription" character varying(22),
    api_id integer NOT NULL
);


ALTER TABLE public.receivers OWNER TO gnss_data_osu;

--
-- Name: receivers_api_id_seq; Type: SEQUENCE; Schema: public; Owner: gnss_data_osu
--

CREATE SEQUENCE public.receivers_api_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.receivers_api_id_seq OWNER TO gnss_data_osu;

--
-- Name: receivers_api_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: gnss_data_osu
--

ALTER SEQUENCE public.receivers_api_id_seq OWNED BY public.receivers.api_id;


--
-- Name: rinex; Type: TABLE; Schema: public; Owner: gnss_data_osu
--

CREATE TABLE public.rinex (
    "NetworkCode" character varying NOT NULL,
    "StationCode" character varying NOT NULL,
    "ObservationYear" numeric NOT NULL,
    "ObservationMonth" numeric NOT NULL,
    "ObservationDay" numeric NOT NULL,
    "ObservationDOY" numeric NOT NULL,
    "ObservationFYear" numeric NOT NULL,
    "ObservationSTime" timestamp without time zone,
    "ObservationETime" timestamp without time zone,
    "ReceiverType" character varying(20),
    "ReceiverSerial" character varying(20),
    "ReceiverFw" character varying(20),
    "AntennaType" character varying(20),
    "AntennaSerial" character varying(20),
    "AntennaDome" character varying(20),
    "Filename" character varying(50),
    "Interval" numeric NOT NULL,
    "AntennaOffset" numeric,
    "Completion" numeric(7,3) NOT NULL,
    api_id integer NOT NULL
);


ALTER TABLE public.rinex OWNER TO gnss_data_osu;

--
-- Name: rinex_api_id_seq; Type: SEQUENCE; Schema: public; Owner: gnss_data_osu
--

CREATE SEQUENCE public.rinex_api_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.rinex_api_id_seq OWNER TO gnss_data_osu;

--
-- Name: rinex_api_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: gnss_data_osu
--

ALTER SEQUENCE public.rinex_api_id_seq OWNED BY public.rinex.api_id;


--
-- Name: rinex_proc; Type: VIEW; Schema: public; Owner: gnss_data_osu
--

CREATE VIEW public.rinex_proc AS
 SELECT rnx."NetworkCode",
    rnx."StationCode",
    rnx."ObservationYear",
    rnx."ObservationMonth",
    rnx."ObservationDay",
    rnx."ObservationDOY",
    rnx."ObservationFYear",
    rnx."ObservationSTime",
    rnx."ObservationETime",
    rnx."ReceiverType",
    rnx."ReceiverSerial",
    rnx."ReceiverFw",
    rnx."AntennaType",
    rnx."AntennaSerial",
    rnx."AntennaDome",
    rnx."Filename",
    rnx."Interval",
    rnx."AntennaOffset",
    rnx."Completion",
    rnx."mI"
   FROM ( SELECT aa."NetworkCode",
            aa."StationCode",
            aa."ObservationYear",
            aa."ObservationMonth",
            aa."ObservationDay",
            aa."ObservationDOY",
            aa."ObservationFYear",
            aa."ObservationSTime",
            aa."ObservationETime",
            aa."ReceiverType",
            aa."ReceiverSerial",
            aa."ReceiverFw",
            aa."AntennaType",
            aa."AntennaSerial",
            aa."AntennaDome",
            aa."Filename",
            aa."Interval",
            aa."AntennaOffset",
            aa."Completion",
            min(aa."Interval") OVER (PARTITION BY aa."NetworkCode", aa."StationCode", aa."ObservationYear", aa."ObservationDOY") AS "mI"
           FROM (public.rinex aa
             LEFT JOIN public.rinex bb ON ((((aa."NetworkCode")::text = (bb."NetworkCode")::text) AND ((aa."StationCode")::text = (bb."StationCode")::text) AND (aa."ObservationYear" = bb."ObservationYear") AND (aa."ObservationDOY" = bb."ObservationDOY") AND (aa."Completion" < bb."Completion"))))
          WHERE (bb."NetworkCode" IS NULL)
          ORDER BY aa."NetworkCode", aa."StationCode", aa."ObservationYear", aa."ObservationDOY", aa."Interval", aa."Completion") rnx
  WHERE (rnx."Interval" = rnx."mI");


ALTER TABLE public.rinex_proc OWNER TO gnss_data_osu;

--
-- Name: rinex_sources_info; Type: TABLE; Schema: public; Owner: gnss_data_osu
--

CREATE TABLE public.rinex_sources_info (
    name character varying(20) NOT NULL,
    fqdn character varying NOT NULL,
    protocol character varying NOT NULL,
    username character varying,
    password character varying,
    path character varying,
    format character varying,
    api_id integer NOT NULL
);


ALTER TABLE public.rinex_sources_info OWNER TO gnss_data_osu;

--
-- Name: rinex_sources_info_api_id_seq; Type: SEQUENCE; Schema: public; Owner: gnss_data_osu
--

CREATE SEQUENCE public.rinex_sources_info_api_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.rinex_sources_info_api_id_seq OWNER TO gnss_data_osu;

--
-- Name: rinex_sources_info_api_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: gnss_data_osu
--

ALTER SEQUENCE public.rinex_sources_info_api_id_seq OWNED BY public.rinex_sources_info.api_id;


--
-- Name: rinex_tank_struct; Type: TABLE; Schema: public; Owner: gnss_data_osu
--

CREATE TABLE public.rinex_tank_struct (
    "Level" integer NOT NULL,
    "KeyCode" character varying(7),
    api_id integer NOT NULL
);


ALTER TABLE public.rinex_tank_struct OWNER TO gnss_data_osu;

--
-- Name: rinex_tank_struct_api_id_seq; Type: SEQUENCE; Schema: public; Owner: gnss_data_osu
--

CREATE SEQUENCE public.rinex_tank_struct_api_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.rinex_tank_struct_api_id_seq OWNER TO gnss_data_osu;

--
-- Name: rinex_tank_struct_api_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: gnss_data_osu
--

ALTER SEQUENCE public.rinex_tank_struct_api_id_seq OWNED BY public.rinex_tank_struct.api_id;


--
-- Name: sources_formats; Type: TABLE; Schema: public; Owner: gnss_data_osu
--

CREATE TABLE public.sources_formats (
    format character varying NOT NULL,
    api_id integer NOT NULL
);


ALTER TABLE public.sources_formats OWNER TO gnss_data_osu;

--
-- Name: sources_formats_api_id_seq; Type: SEQUENCE; Schema: public; Owner: gnss_data_osu
--

CREATE SEQUENCE public.sources_formats_api_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.sources_formats_api_id_seq OWNER TO gnss_data_osu;

--
-- Name: sources_formats_api_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: gnss_data_osu
--

ALTER SEQUENCE public.sources_formats_api_id_seq OWNED BY public.sources_formats.api_id;


--
-- Name: sources_servers; Type: TABLE; Schema: public; Owner: gnss_data_osu
--

CREATE TABLE public.sources_servers (
    server_id integer NOT NULL,
    protocol character varying NOT NULL,
    fqdn character varying NOT NULL,
    username character varying,
    password character varying,
    path character varying,
    format character varying DEFAULT 'DEFAULT_FORMAT'::character varying NOT NULL,
    CONSTRAINT sources_servers_protocol_check CHECK (((protocol)::text = ANY (ARRAY[('ftp'::character varying)::text, ('http'::character varying)::text, ('sftp'::character varying)::text, ('https'::character varying)::text, ('ftpa'::character varying)::text, ('FTP'::character varying)::text, ('HTTP'::character varying)::text, ('SFTP'::character varying)::text, ('HTTPS'::character varying)::text, ('FTPA'::character varying)::text])))
);


ALTER TABLE public.sources_servers OWNER TO gnss_data_osu;

--
-- Name: sources_servers_server_id_seq; Type: SEQUENCE; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE public.sources_servers ALTER COLUMN server_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.sources_servers_server_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: sources_stations; Type: TABLE; Schema: public; Owner: gnss_data_osu
--

CREATE TABLE public.sources_stations (
    "NetworkCode" character varying(3) NOT NULL,
    "StationCode" character varying(4) NOT NULL,
    try_order smallint DEFAULT 1 NOT NULL,
    server_id integer NOT NULL,
    path character varying,
    format character varying,
    api_id integer NOT NULL
);


ALTER TABLE public.sources_stations OWNER TO gnss_data_osu;

--
-- Name: sources_stations_api_id_seq; Type: SEQUENCE; Schema: public; Owner: gnss_data_osu
--

CREATE SEQUENCE public.sources_stations_api_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.sources_stations_api_id_seq OWNER TO gnss_data_osu;

--
-- Name: sources_stations_api_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: gnss_data_osu
--

ALTER SEQUENCE public.sources_stations_api_id_seq OWNED BY public.sources_stations.api_id;


--
-- Name: stacks; Type: TABLE; Schema: public; Owner: gnss_data_osu
--

CREATE TABLE public.stacks (
    "NetworkCode" character varying(3) NOT NULL,
    "StationCode" character varying(4) NOT NULL,
    "Project" character varying(20) NOT NULL,
    "Year" numeric NOT NULL,
    "DOY" numeric NOT NULL,
    "FYear" numeric,
    "X" numeric,
    "Y" numeric,
    "Z" numeric,
    sigmax numeric,
    sigmay numeric,
    sigmaz numeric,
    "VarianceFactor" numeric,
    sigmaxy numeric,
    sigmayz numeric,
    sigmaxz numeric,
    name character varying(20) NOT NULL,
    api_id integer NOT NULL
);


ALTER TABLE public.stacks OWNER TO gnss_data_osu;

--
-- Name: stacks_api_id_seq; Type: SEQUENCE; Schema: public; Owner: gnss_data_osu
--

CREATE SEQUENCE public.stacks_api_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.stacks_api_id_seq OWNER TO gnss_data_osu;

--
-- Name: stacks_api_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: gnss_data_osu
--

ALTER SEQUENCE public.stacks_api_id_seq OWNED BY public.stacks.api_id;


--
-- Name: stationalias; Type: TABLE; Schema: public; Owner: gnss_data_osu
--

CREATE TABLE public.stationalias (
    "NetworkCode" character varying(3) NOT NULL,
    "StationCode" character varying(4) NOT NULL,
    "StationAlias" character varying(4) NOT NULL,
    api_id integer NOT NULL
);


ALTER TABLE public.stationalias OWNER TO gnss_data_osu;

--
-- Name: stationalias_api_id_seq; Type: SEQUENCE; Schema: public; Owner: gnss_data_osu
--

CREATE SEQUENCE public.stationalias_api_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.stationalias_api_id_seq OWNER TO gnss_data_osu;

--
-- Name: stationalias_api_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: gnss_data_osu
--

ALTER SEQUENCE public.stationalias_api_id_seq OWNED BY public.stationalias.api_id;


--
-- Name: stationinfo; Type: TABLE; Schema: public; Owner: gnss_data_osu
--

CREATE TABLE public.stationinfo (
    "NetworkCode" character varying(3) NOT NULL,
    "StationCode" character varying(4) NOT NULL,
    "ReceiverCode" character varying(22) NOT NULL,
    "ReceiverSerial" character varying(22),
    "ReceiverFirmware" character varying(10),
    "AntennaCode" character varying(22) NOT NULL,
    "AntennaSerial" character varying(20),
    "AntennaHeight" numeric(6,4) DEFAULT 0 NOT NULL,
    "AntennaNorth" numeric(12,4) DEFAULT 0 NOT NULL,
    "AntennaEast" numeric(12,4) DEFAULT 0 NOT NULL,
    "HeightCode" character varying,
    "RadomeCode" character varying(7) NOT NULL,
    "DateStart" timestamp without time zone NOT NULL,
    "DateEnd" timestamp without time zone,
    "ReceiverVers" character varying(22),
    "Comments" text,
    api_id integer NOT NULL,
    antdaz numeric(4,1)
);


ALTER TABLE public.stationinfo OWNER TO gnss_data_osu;

--
-- Name: stationinfo_api_id_seq; Type: SEQUENCE; Schema: public; Owner: gnss_data_osu
--

CREATE SEQUENCE public.stationinfo_api_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.stationinfo_api_id_seq OWNER TO gnss_data_osu;

--
-- Name: stationinfo_api_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: gnss_data_osu
--

ALTER SEQUENCE public.stationinfo_api_id_seq OWNED BY public.stationinfo.api_id;


--
-- Name: stations; Type: TABLE; Schema: public; Owner: gnss_data_osu
--

CREATE TABLE public.stations (
    "NetworkCode" character varying(3) NOT NULL,
    "StationCode" character varying(4) NOT NULL,
    "StationName" character varying(40),
    "DateStart" numeric(7,3),
    "DateEnd" numeric(7,3),
    auto_x numeric,
    auto_y numeric,
    auto_z numeric,
    "Harpos_coeff_otl" text,
    lat numeric,
    lon numeric,
    height numeric,
    max_dist numeric,
    dome character varying(9),
    country_code character varying(3),
    marker integer,
    alias character varying(4),
    api_id integer NOT NULL
);


ALTER TABLE public.stations OWNER TO gnss_data_osu;

--
-- Name: stations_api_id_seq; Type: SEQUENCE; Schema: public; Owner: gnss_data_osu
--

CREATE SEQUENCE public.stations_api_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.stations_api_id_seq OWNER TO gnss_data_osu;

--
-- Name: stations_api_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: gnss_data_osu
--

ALTER SEQUENCE public.stations_api_id_seq OWNED BY public.stations.api_id;


--
-- Name: antennas api_id; Type: DEFAULT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.antennas ALTER COLUMN api_id SET DEFAULT nextval('public.antennas_api_id_seq'::regclass);


--
-- Name: apr_coords api_id; Type: DEFAULT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.apr_coords ALTER COLUMN api_id SET DEFAULT nextval('public.apr_coords_api_id_seq'::regclass);


--
-- Name: aws_sync api_id; Type: DEFAULT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.aws_sync ALTER COLUMN api_id SET DEFAULT nextval('public.aws_sync_api_id_seq'::regclass);


--
-- Name: data_source api_id; Type: DEFAULT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.data_source ALTER COLUMN api_id SET DEFAULT nextval('public.data_source_api_id_seq'::regclass);


--
-- Name: earthquakes api_id; Type: DEFAULT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.earthquakes ALTER COLUMN api_id SET DEFAULT nextval('public.earthquakes_api_id_seq'::regclass);


--
-- Name: executions api_id; Type: DEFAULT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.executions ALTER COLUMN api_id SET DEFAULT nextval('public.executions_api_id_seq'::regclass);


--
-- Name: gamit_htc api_id; Type: DEFAULT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.gamit_htc ALTER COLUMN api_id SET DEFAULT nextval('public.gamit_htc_api_id_seq'::regclass);


--
-- Name: gamit_soln api_id; Type: DEFAULT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.gamit_soln ALTER COLUMN api_id SET DEFAULT nextval('public.gamit_soln_api_id_seq'::regclass);


--
-- Name: gamit_soln_excl api_id; Type: DEFAULT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.gamit_soln_excl ALTER COLUMN api_id SET DEFAULT nextval('public.gamit_soln_excl_api_id_seq'::regclass);


--
-- Name: gamit_stats api_id; Type: DEFAULT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.gamit_stats ALTER COLUMN api_id SET DEFAULT nextval('public.gamit_stats_api_id_seq'::regclass);


--
-- Name: gamit_subnets api_id; Type: DEFAULT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.gamit_subnets ALTER COLUMN api_id SET DEFAULT nextval('public.gamit_subnets_api_id_seq'::regclass);


--
-- Name: gamit_ztd api_id; Type: DEFAULT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.gamit_ztd ALTER COLUMN api_id SET DEFAULT nextval('public.gamit_ztd_api_id_seq'::regclass);


--
-- Name: keys api_id; Type: DEFAULT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.keys ALTER COLUMN api_id SET DEFAULT nextval('public.keys_api_id_seq'::regclass);


--
-- Name: locks api_id; Type: DEFAULT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.locks ALTER COLUMN api_id SET DEFAULT nextval('public.locks_api_id_seq'::regclass);


--
-- Name: networks api_id; Type: DEFAULT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.networks ALTER COLUMN api_id SET DEFAULT nextval('public.networks_api_id_seq'::regclass);


--
-- Name: ppp_soln api_id; Type: DEFAULT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.ppp_soln ALTER COLUMN api_id SET DEFAULT nextval('public.ppp_soln_api_id_seq'::regclass);


--
-- Name: ppp_soln_excl api_id; Type: DEFAULT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.ppp_soln_excl ALTER COLUMN api_id SET DEFAULT nextval('public.ppp_soln_excl_api_id_seq'::regclass);


--
-- Name: receivers api_id; Type: DEFAULT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.receivers ALTER COLUMN api_id SET DEFAULT nextval('public.receivers_api_id_seq'::regclass);


--
-- Name: rinex api_id; Type: DEFAULT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.rinex ALTER COLUMN api_id SET DEFAULT nextval('public.rinex_api_id_seq'::regclass);


--
-- Name: rinex_sources_info api_id; Type: DEFAULT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.rinex_sources_info ALTER COLUMN api_id SET DEFAULT nextval('public.rinex_sources_info_api_id_seq'::regclass);


--
-- Name: rinex_tank_struct api_id; Type: DEFAULT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.rinex_tank_struct ALTER COLUMN api_id SET DEFAULT nextval('public.rinex_tank_struct_api_id_seq'::regclass);


--
-- Name: sources_formats api_id; Type: DEFAULT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.sources_formats ALTER COLUMN api_id SET DEFAULT nextval('public.sources_formats_api_id_seq'::regclass);


--
-- Name: sources_stations api_id; Type: DEFAULT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.sources_stations ALTER COLUMN api_id SET DEFAULT nextval('public.sources_stations_api_id_seq'::regclass);


--
-- Name: stacks api_id; Type: DEFAULT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.stacks ALTER COLUMN api_id SET DEFAULT nextval('public.stacks_api_id_seq'::regclass);


--
-- Name: stationalias api_id; Type: DEFAULT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.stationalias ALTER COLUMN api_id SET DEFAULT nextval('public.stationalias_api_id_seq'::regclass);


--
-- Name: stationinfo api_id; Type: DEFAULT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.stationinfo ALTER COLUMN api_id SET DEFAULT nextval('public.stationinfo_api_id_seq'::regclass);


--
-- Name: stations api_id; Type: DEFAULT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.stations ALTER COLUMN api_id SET DEFAULT nextval('public.stations_api_id_seq'::regclass);


--
-- Name: antennas antennas_api_id_key; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.antennas
    ADD CONSTRAINT antennas_api_id_key UNIQUE (api_id);


--
-- Name: antennas antennas_pkey; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.antennas
    ADD CONSTRAINT antennas_pkey PRIMARY KEY ("AntennaCode");


--
-- Name: apr_coords apr_coords_api_id_key; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.apr_coords
    ADD CONSTRAINT apr_coords_api_id_key UNIQUE (api_id);


--
-- Name: apr_coords apr_coords_pkey; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.apr_coords
    ADD CONSTRAINT apr_coords_pkey PRIMARY KEY ("NetworkCode", "StationCode", "Year", "DOY");


--
-- Name: aws_sync aws_sync_api_id_key; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.aws_sync
    ADD CONSTRAINT aws_sync_api_id_key UNIQUE (api_id);


--
-- Name: aws_sync aws_sync_pkey; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.aws_sync
    ADD CONSTRAINT aws_sync_pkey PRIMARY KEY ("NetworkCode", "StationCode", "Year", "DOY");


--
-- Name: data_source data_source_api_id_key; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.data_source
    ADD CONSTRAINT data_source_api_id_key UNIQUE (api_id);


--
-- Name: data_source data_source_pkey; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.data_source
    ADD CONSTRAINT data_source_pkey PRIMARY KEY ("NetworkCode", "StationCode", try_order);


--
-- Name: stationinfo date_chk; Type: CHECK CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE public.stationinfo
    ADD CONSTRAINT date_chk CHECK (("DateEnd" > "DateStart")) NOT VALID;


--
-- Name: earthquakes earthquakes_api_id_key; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.earthquakes
    ADD CONSTRAINT earthquakes_api_id_key UNIQUE (api_id);


--
-- Name: earthquakes earthquakes_pkey; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.earthquakes
    ADD CONSTRAINT earthquakes_pkey PRIMARY KEY (date, lat, lon);


--
-- Name: etm_params etm_params_pkey; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.etm_params
    ADD CONSTRAINT etm_params_pkey PRIMARY KEY (uid);


--
-- Name: etms etmsv2_pkey; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.etms
    ADD CONSTRAINT etmsv2_pkey PRIMARY KEY (uid);


--
-- Name: events events_pkey; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.events
    ADD CONSTRAINT events_pkey PRIMARY KEY (event_id, "EventDate");


--
-- Name: executions executions_api_id_key; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.executions
    ADD CONSTRAINT executions_api_id_key UNIQUE (api_id);


--
-- Name: gamit_htc gamit_htc_api_id_key; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.gamit_htc
    ADD CONSTRAINT gamit_htc_api_id_key UNIQUE (api_id);


--
-- Name: gamit_htc gamit_htc_pkey; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.gamit_htc
    ADD CONSTRAINT gamit_htc_pkey PRIMARY KEY ("AntennaCode", "HeightCode");


--
-- Name: gamit_soln gamit_soln_api_id_key; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.gamit_soln
    ADD CONSTRAINT gamit_soln_api_id_key UNIQUE (api_id);


--
-- Name: gamit_soln_excl gamit_soln_excl_api_id_key; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.gamit_soln_excl
    ADD CONSTRAINT gamit_soln_excl_api_id_key UNIQUE (api_id);


--
-- Name: gamit_soln_excl gamit_soln_excl_pkey; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.gamit_soln_excl
    ADD CONSTRAINT gamit_soln_excl_pkey PRIMARY KEY ("NetworkCode", "StationCode", "Project", "Year", "DOY");


--
-- Name: gamit_soln gamit_soln_pkey; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.gamit_soln
    ADD CONSTRAINT gamit_soln_pkey PRIMARY KEY ("NetworkCode", "StationCode", "Project", "Year", "DOY");


--
-- Name: gamit_stats gamit_stats_api_id_key; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.gamit_stats
    ADD CONSTRAINT gamit_stats_api_id_key UNIQUE (api_id);


--
-- Name: gamit_stats gamit_stats_pkey; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.gamit_stats
    ADD CONSTRAINT gamit_stats_pkey PRIMARY KEY ("Project", subnet, "Year", "DOY", system);


--
-- Name: gamit_subnets gamit_subnets_api_id_key; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.gamit_subnets
    ADD CONSTRAINT gamit_subnets_api_id_key UNIQUE (api_id);


--
-- Name: gamit_subnets gamit_subnets_pkey; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.gamit_subnets
    ADD CONSTRAINT gamit_subnets_pkey PRIMARY KEY ("Project", subnet, "Year", "DOY");


--
-- Name: gamit_ztd gamit_ztd_api_id_key; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.gamit_ztd
    ADD CONSTRAINT gamit_ztd_api_id_key UNIQUE (api_id);


--
-- Name: gamit_ztd gamit_ztd_pkey; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.gamit_ztd
    ADD CONSTRAINT gamit_ztd_pkey PRIMARY KEY ("NetworkCode", "StationCode", "Date", "Project", "Year", "DOY");


--
-- Name: keys keys_api_id_key; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.keys
    ADD CONSTRAINT keys_api_id_key UNIQUE (api_id);


--
-- Name: keys keys_pkey; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.keys
    ADD CONSTRAINT keys_pkey PRIMARY KEY ("KeyCode");


--
-- Name: locks locks_api_id_key; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.locks
    ADD CONSTRAINT locks_api_id_key UNIQUE (api_id);


--
-- Name: locks locks_pkey; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.locks
    ADD CONSTRAINT locks_pkey PRIMARY KEY (filename);


--
-- Name: networks networks_NetworkCode_pkey; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.networks
    ADD CONSTRAINT "networks_NetworkCode_pkey" PRIMARY KEY ("NetworkCode");


--
-- Name: networks networks_api_id_key; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.networks
    ADD CONSTRAINT networks_api_id_key UNIQUE (api_id);


--
-- Name: ppp_soln ppp_soln_api_id_key; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.ppp_soln
    ADD CONSTRAINT ppp_soln_api_id_key UNIQUE (api_id);


--
-- Name: ppp_soln_excl ppp_soln_excl_api_id_key; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.ppp_soln_excl
    ADD CONSTRAINT ppp_soln_excl_api_id_key UNIQUE (api_id);


--
-- Name: ppp_soln_excl ppp_soln_excl_pkey; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.ppp_soln_excl
    ADD CONSTRAINT ppp_soln_excl_pkey PRIMARY KEY ("NetworkCode", "StationCode", "Year", "DOY");


--
-- Name: ppp_soln ppp_soln_pkey; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.ppp_soln
    ADD CONSTRAINT ppp_soln_pkey PRIMARY KEY ("NetworkCode", "StationCode", "Year", "DOY", "ReferenceFrame");


--
-- Name: receivers receivers_ReceiverCode_pkey; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.receivers
    ADD CONSTRAINT "receivers_ReceiverCode_pkey" PRIMARY KEY ("ReceiverCode");


--
-- Name: receivers receivers_api_id_key; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.receivers
    ADD CONSTRAINT receivers_api_id_key UNIQUE (api_id);


--
-- Name: rinex rinex_api_id_key; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.rinex
    ADD CONSTRAINT rinex_api_id_key UNIQUE (api_id);


--
-- Name: rinex rinex_pkey; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.rinex
    ADD CONSTRAINT rinex_pkey PRIMARY KEY ("NetworkCode", "StationCode", "ObservationYear", "ObservationDOY", "Interval", "Completion");


--
-- Name: rinex_sources_info rinex_sources_info_api_id_key; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.rinex_sources_info
    ADD CONSTRAINT rinex_sources_info_api_id_key UNIQUE (api_id);


--
-- Name: rinex_sources_info rinex_sources_info_pkey; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.rinex_sources_info
    ADD CONSTRAINT rinex_sources_info_pkey PRIMARY KEY (name);


--
-- Name: rinex_tank_struct rinex_tank_struct_api_id_key; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.rinex_tank_struct
    ADD CONSTRAINT rinex_tank_struct_api_id_key UNIQUE (api_id);


--
-- Name: rinex_tank_struct rinex_tank_struct_pkey; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.rinex_tank_struct
    ADD CONSTRAINT rinex_tank_struct_pkey PRIMARY KEY ("Level");


--
-- Name: sources_formats sources_formats_api_id_key; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.sources_formats
    ADD CONSTRAINT sources_formats_api_id_key UNIQUE (api_id);


--
-- Name: sources_formats sources_formats_pkey; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.sources_formats
    ADD CONSTRAINT sources_formats_pkey PRIMARY KEY (format);


--
-- Name: sources_servers sources_servers_pkey; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.sources_servers
    ADD CONSTRAINT sources_servers_pkey PRIMARY KEY (server_id);


--
-- Name: sources_stations sources_stations_api_id_key; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.sources_stations
    ADD CONSTRAINT sources_stations_api_id_key UNIQUE (api_id);


--
-- Name: sources_stations sources_stations_pkey; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.sources_stations
    ADD CONSTRAINT sources_stations_pkey PRIMARY KEY ("NetworkCode", "StationCode", try_order);


--
-- Name: stacks stacks_api_id_key; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.stacks
    ADD CONSTRAINT stacks_api_id_key UNIQUE (api_id);


--
-- Name: stacks stacks_pkey; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.stacks
    ADD CONSTRAINT stacks_pkey PRIMARY KEY ("NetworkCode", "StationCode", "Year", "DOY", name);


--
-- Name: stationalias stationalias_api_id_key; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.stationalias
    ADD CONSTRAINT stationalias_api_id_key UNIQUE (api_id);


--
-- Name: stationalias stationalias_pkey; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.stationalias
    ADD CONSTRAINT stationalias_pkey PRIMARY KEY ("NetworkCode", "StationCode");


--
-- Name: stationalias stationalias_uniq; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.stationalias
    ADD CONSTRAINT stationalias_uniq UNIQUE ("StationAlias");


--
-- Name: stationinfo stationinfo_NetworkCode_StationCode_pkey; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.stationinfo
    ADD CONSTRAINT "stationinfo_NetworkCode_StationCode_pkey" PRIMARY KEY ("NetworkCode", "StationCode", "DateStart");


--
-- Name: stationinfo stationinfo_api_id_key; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.stationinfo
    ADD CONSTRAINT stationinfo_api_id_key UNIQUE (api_id);


--
-- Name: stations stations_NetworkCode_StationCode_pkey; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.stations
    ADD CONSTRAINT "stations_NetworkCode_StationCode_pkey" PRIMARY KEY ("NetworkCode", "StationCode");


--
-- Name: stations stations_api_id_key; Type: CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.stations
    ADD CONSTRAINT stations_api_id_key UNIQUE (api_id);


--
-- Name: Filename; Type: INDEX; Schema: public; Owner: gnss_data_osu
--

CREATE INDEX "Filename" ON public.rinex USING btree ("Filename" varchar_ops);


--
-- Name: apr_coords_date_idx; Type: INDEX; Schema: public; Owner: gnss_data_osu
--

CREATE INDEX apr_coords_date_idx ON public.apr_coords USING btree ("NetworkCode", "StationCode", "Year", "DOY");


--
-- Name: aws_sync_idx; Type: INDEX; Schema: public; Owner: gnss_data_osu
--

CREATE INDEX aws_sync_idx ON public.aws_sync USING btree ("NetworkCode", "StationCode", "StationAlias", "Year", "DOY");


--
-- Name: etm_params_idx; Type: INDEX; Schema: public; Owner: gnss_data_osu
--

CREATE INDEX etm_params_idx ON public.etm_params USING btree ("NetworkCode", "StationCode", soln, object);


--
-- Name: events_index; Type: INDEX; Schema: public; Owner: gnss_data_osu
--

CREATE INDEX events_index ON public.events USING btree ("NetworkCode", "StationCode", "Year", "DOY");


--
-- Name: gamit_ztd_idx; Type: INDEX; Schema: public; Owner: gnss_data_osu
--

CREATE INDEX gamit_ztd_idx ON public.gamit_ztd USING btree ("Project", "Year", "DOY");


--
-- Name: network_station; Type: INDEX; Schema: public; Owner: gnss_data_osu
--

CREATE INDEX network_station ON public.rinex USING btree ("NetworkCode" varchar_ops, "StationCode" varchar_ops);


--
-- Name: ppp_soln_idx; Type: INDEX; Schema: public; Owner: gnss_data_osu
--

CREATE INDEX ppp_soln_idx ON public.ppp_soln USING btree ("NetworkCode" COLLATE "C" varchar_ops, "StationCode" COLLATE "C" varchar_ops);


--
-- Name: ppp_soln_order; Type: INDEX; Schema: public; Owner: gnss_data_osu
--

CREATE INDEX ppp_soln_order ON public.ppp_soln USING btree ("NetworkCode", "StationCode", "Year", "DOY");


--
-- Name: rinex_obs_comp_idx; Type: INDEX; Schema: public; Owner: gnss_data_osu
--

CREATE INDEX rinex_obs_comp_idx ON public.rinex USING btree ("NetworkCode", "StationCode", "ObservationYear", "ObservationDOY", "Completion");


--
-- Name: rinex_obs_idx; Type: INDEX; Schema: public; Owner: gnss_data_osu
--

CREATE INDEX rinex_obs_idx ON public.rinex USING btree ("NetworkCode", "StationCode", "ObservationYear", "ObservationDOY");


--
-- Name: stacks_idx; Type: INDEX; Schema: public; Owner: gnss_data_osu
--

CREATE INDEX stacks_idx ON public.stacks USING btree ("Year", "DOY");


--
-- Name: stations_NetworkCode_StationCode_idx; Type: INDEX; Schema: public; Owner: gnss_data_osu
--

CREATE UNIQUE INDEX "stations_NetworkCode_StationCode_idx" ON public.stations USING btree ("NetworkCode", "StationCode");


--
-- Name: stations_country_code_idx; Type: INDEX; Schema: public; Owner: gnss_data_osu
--

CREATE INDEX stations_country_code_idx ON public.stations USING btree (country_code);


--
-- Name: rinex update_stations; Type: TRIGGER; Schema: public; Owner: gnss_data_osu
--

CREATE TRIGGER update_stations AFTER INSERT ON public.rinex FOR EACH ROW EXECUTE FUNCTION public.update_timespan_trigg();


--
-- Name: stationalias verify_stationalias; Type: TRIGGER; Schema: public; Owner: gnss_data_osu
--

CREATE TRIGGER verify_stationalias BEFORE INSERT OR UPDATE ON public.stationalias FOR EACH ROW EXECUTE FUNCTION public.stationalias_check();


--
-- Name: stations NetworkCode; Type: FK CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.stations
    ADD CONSTRAINT "NetworkCode" FOREIGN KEY ("NetworkCode") REFERENCES public.networks("NetworkCode") MATCH FULL ON UPDATE CASCADE ON DELETE RESTRICT;


--
-- Name: gamit_htc antenna_fk; Type: FK CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.gamit_htc
    ADD CONSTRAINT antenna_fk FOREIGN KEY ("AntennaCode") REFERENCES public.antennas("AntennaCode") ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: apr_coords apr_coords_NetworkCode_fkey; Type: FK CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.apr_coords
    ADD CONSTRAINT "apr_coords_NetworkCode_fkey" FOREIGN KEY ("NetworkCode", "StationCode") REFERENCES public.stations("NetworkCode", "StationCode");


--
-- Name: data_source data_source_NetworkCode_fkey; Type: FK CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.data_source
    ADD CONSTRAINT "data_source_NetworkCode_fkey" FOREIGN KEY ("NetworkCode", "StationCode") REFERENCES public.stations("NetworkCode", "StationCode") ON UPDATE CASCADE ON DELETE RESTRICT;


--
-- Name: etms etms_fk; Type: FK CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.etms
    ADD CONSTRAINT etms_fk FOREIGN KEY ("NetworkCode", "StationCode") REFERENCES public.stations("NetworkCode", "StationCode") ON UPDATE CASCADE ON DELETE RESTRICT;


--
-- Name: gamit_soln gamit_soln_NetworkCode_fkey; Type: FK CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.gamit_soln
    ADD CONSTRAINT "gamit_soln_NetworkCode_fkey" FOREIGN KEY ("NetworkCode", "StationCode") REFERENCES public.stations("NetworkCode", "StationCode") ON UPDATE CASCADE ON DELETE RESTRICT;


--
-- Name: gamit_soln_excl gamit_soln_excl_NetworkCode_fkey; Type: FK CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.gamit_soln_excl
    ADD CONSTRAINT "gamit_soln_excl_NetworkCode_fkey" FOREIGN KEY ("NetworkCode", "StationCode", "Project", "Year", "DOY") REFERENCES public.gamit_soln("NetworkCode", "StationCode", "Project", "Year", "DOY") ON UPDATE CASCADE ON DELETE CASCADE NOT VALID;


--
-- Name: gamit_ztd gamit_ztd_NetworkCode_fkey; Type: FK CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.gamit_ztd
    ADD CONSTRAINT "gamit_ztd_NetworkCode_fkey" FOREIGN KEY ("NetworkCode", "StationCode") REFERENCES public.stations("NetworkCode", "StationCode");


--
-- Name: locks locks_NetworkCode_fkey; Type: FK CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.locks
    ADD CONSTRAINT "locks_NetworkCode_fkey" FOREIGN KEY ("NetworkCode", "StationCode") REFERENCES public.stations("NetworkCode", "StationCode") ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: ppp_soln ppp_soln_NetworkName_StationCode_fkey; Type: FK CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.ppp_soln
    ADD CONSTRAINT "ppp_soln_NetworkName_StationCode_fkey" FOREIGN KEY ("NetworkCode", "StationCode") REFERENCES public.stations("NetworkCode", "StationCode") ON UPDATE CASCADE ON DELETE RESTRICT;


--
-- Name: ppp_soln_excl ppp_soln_excl_NetworkCode_fkey; Type: FK CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.ppp_soln_excl
    ADD CONSTRAINT "ppp_soln_excl_NetworkCode_fkey" FOREIGN KEY ("NetworkCode", "StationCode") REFERENCES public.stations("NetworkCode", "StationCode") ON UPDATE CASCADE ON DELETE RESTRICT;


--
-- Name: rinex rinex_NetworkCode_fkey; Type: FK CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.rinex
    ADD CONSTRAINT "rinex_NetworkCode_fkey" FOREIGN KEY ("NetworkCode", "StationCode") REFERENCES public.stations("NetworkCode", "StationCode") ON UPDATE CASCADE ON DELETE RESTRICT;


--
-- Name: rinex_tank_struct rinex_tank_struct_key_fkey; Type: FK CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.rinex_tank_struct
    ADD CONSTRAINT rinex_tank_struct_key_fkey FOREIGN KEY ("KeyCode") REFERENCES public.keys("KeyCode");


--
-- Name: sources_servers sources_servers_format_fkey; Type: FK CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.sources_servers
    ADD CONSTRAINT sources_servers_format_fkey FOREIGN KEY (format) REFERENCES public.sources_formats(format);


--
-- Name: sources_stations sources_stations_NetworkCode_StationCode_fkey; Type: FK CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.sources_stations
    ADD CONSTRAINT "sources_stations_NetworkCode_StationCode_fkey" FOREIGN KEY ("NetworkCode", "StationCode") REFERENCES public.stations("NetworkCode", "StationCode");


--
-- Name: sources_stations sources_stations_format_fkey; Type: FK CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.sources_stations
    ADD CONSTRAINT sources_stations_format_fkey FOREIGN KEY (format) REFERENCES public.sources_formats(format);


--
-- Name: sources_stations sources_stations_server_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.sources_stations
    ADD CONSTRAINT sources_stations_server_id_fkey FOREIGN KEY (server_id) REFERENCES public.sources_servers(server_id);


--
-- Name: stacks stacks_NetworkCode_fkey; Type: FK CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.stacks
    ADD CONSTRAINT "stacks_NetworkCode_fkey" FOREIGN KEY ("NetworkCode", "StationCode") REFERENCES public.stations("NetworkCode", "StationCode") ON UPDATE CASCADE ON DELETE RESTRICT;


--
-- Name: stacks stacks_gamit_soln_fkey; Type: FK CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.stacks
    ADD CONSTRAINT stacks_gamit_soln_fkey FOREIGN KEY ("Year", "DOY", "StationCode", "Project", "NetworkCode") REFERENCES public.gamit_soln("Year", "DOY", "StationCode", "Project", "NetworkCode") ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: stationalias stationalias_fk; Type: FK CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.stationalias
    ADD CONSTRAINT stationalias_fk FOREIGN KEY ("NetworkCode", "StationCode") REFERENCES public.stations("NetworkCode", "StationCode") ON UPDATE CASCADE ON DELETE RESTRICT;


--
-- Name: stationinfo stationinfo_AntennaCode_fkey; Type: FK CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.stationinfo
    ADD CONSTRAINT "stationinfo_AntennaCode_fkey" FOREIGN KEY ("AntennaCode") REFERENCES public.antennas("AntennaCode") ON UPDATE CASCADE ON DELETE RESTRICT;


--
-- Name: stationinfo stationinfo_NetworkCode_StationCode_fkey; Type: FK CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.stationinfo
    ADD CONSTRAINT "stationinfo_NetworkCode_StationCode_fkey" FOREIGN KEY ("NetworkCode", "StationCode") REFERENCES public.stations("NetworkCode", "StationCode") ON UPDATE CASCADE ON DELETE RESTRICT;


--
-- Name: stationinfo stationinfo_ReceiverCode_fkey; Type: FK CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.stationinfo
    ADD CONSTRAINT "stationinfo_ReceiverCode_fkey" FOREIGN KEY ("ReceiverCode") REFERENCES public.receivers("ReceiverCode") ON UPDATE CASCADE ON DELETE RESTRICT;


--
-- Name: etm_params stations_fk; Type: FK CONSTRAINT; Schema: public; Owner: gnss_data_osu
--

ALTER TABLE ONLY public.etm_params
    ADD CONSTRAINT stations_fk FOREIGN KEY ("NetworkCode", "StationCode") REFERENCES public.stations("NetworkCode", "StationCode");


--
-- Name: SCHEMA public; Type: ACL; Schema: -; Owner: postgres
--

REVOKE USAGE ON SCHEMA public FROM PUBLIC;
GRANT ALL ON SCHEMA public TO PUBLIC;


--
-- PostgreSQL database dump complete
--

\unrestrict tGsY5bCIEbTzTAWJwc6PTXrTmC61di1BDkxj3jbadNdsNgE9Gl4yYCM6BMZ5jx6

