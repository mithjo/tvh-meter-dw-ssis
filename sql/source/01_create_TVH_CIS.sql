/* =============================================================================
   TVH_CIS - the (fictional) Customer Information System of Trillium Valley Hydro.
   This database plays the SOURCE system. The ETL only ever reads from it.

   Run order:
     1. this script                      (drops and recreates TVH_CIS)
     2. simulator/output/cis_seed/load_seed.sql   (written by generate_sources.py)
     3. daily: python deliver.py ...     (applies cis_changes/<date>.sql)

   Design notes for your docs:
   - CIS stores date-times in LOCAL Ontario time without an offset (a legacy habit).
     The warehouse must convert them to UTC.
   - Change Tracking is on, so the ETL can ask "what changed since version N?".
   - Snapshot isolation is on, so an extract can read one consistent version.
   - Column order matches the seed CSV files exactly (BULK INSERT maps by position).
   ============================================================================= */
USE master;
GO
IF DB_ID(N'TVH_CIS') IS NOT NULL
BEGIN
    ALTER DATABASE TVH_CIS SET SINGLE_USER WITH ROLLBACK IMMEDIATE;
    DROP DATABASE TVH_CIS;
END;
GO
CREATE DATABASE TVH_CIS;
GO
ALTER DATABASE TVH_CIS SET RECOVERY SIMPLE;
ALTER DATABASE TVH_CIS SET ALLOW_SNAPSHOT_ISOLATION ON;
ALTER DATABASE TVH_CIS SET CHANGE_TRACKING = ON (CHANGE_RETENTION = 90 DAYS, AUTO_CLEANUP = ON);
GO
USE TVH_CIS;
GO
CREATE SCHEMA cis AUTHORIZATION dbo;
GO

/* ---------- reference data ---------- */
CREATE TABLE cis.RatePlan (
    RatePlanCode    varchar(10)    NOT NULL CONSTRAINT PK_RatePlan PRIMARY KEY,
    RatePlanName    nvarchar(50)   NOT NULL,
    Description     nvarchar(200)  NULL
);

CREATE TABLE cis.Season (
    SeasonCode      varchar(10)    NOT NULL CONSTRAINT PK_Season PRIMARY KEY,
    StartMonth      tinyint        NOT NULL,
    StartDay        tinyint        NOT NULL,
    EndMonth        tinyint        NOT NULL,
    EndDay          tinyint        NOT NULL
);

CREATE TABLE cis.Holiday (
    HolidayDate     date           NOT NULL CONSTRAINT PK_Holiday PRIMARY KEY,
    HolidayName     nvarchar(60)   NOT NULL
);

CREATE TABLE cis.RatePrice (
    RatePriceId     int            NOT NULL CONSTRAINT PK_RatePrice PRIMARY KEY,
    RatePlanCode    varchar(10)    NOT NULL CONSTRAINT FK_RatePrice_RatePlan REFERENCES cis.RatePlan (RatePlanCode),
    PeriodCode      varchar(5)     NOT NULL,      -- ON, MID, OFF, ULO, T1, T2
    CentsPerKwh     decimal(7,3)   NOT NULL,
    TierLimitKwh    int            NULL,          -- monthly limit for T1 rows only
    EffectiveFrom   date           NOT NULL,
    EffectiveTo     date           NULL           -- inclusive; NULL = still in effect
);

CREATE TABLE cis.TouWindow (
    TouWindowId     int            NOT NULL CONSTRAINT PK_TouWindow PRIMARY KEY,
    RatePlanCode    varchar(10)    NOT NULL CONSTRAINT FK_TouWindow_RatePlan REFERENCES cis.RatePlan (RatePlanCode),
    SeasonCode      varchar(10)    NOT NULL CONSTRAINT FK_TouWindow_Season REFERENCES cis.Season (SeasonCode),
    DayType         varchar(20)    NOT NULL,      -- WEEKDAY or WEEKEND_HOLIDAY
    HourStart       tinyint        NOT NULL,      -- local clock hour, inclusive
    HourEnd         tinyint        NOT NULL,      -- local clock hour, exclusive
    PeriodCode      varchar(5)     NOT NULL
);

/* ---------- customers, premises, accounts ---------- */
CREATE TABLE cis.Customer (
    CustomerId      int            NOT NULL CONSTRAINT PK_Customer PRIMARY KEY,
    CustomerNumber  varchar(10)    NOT NULL CONSTRAINT UQ_Customer_Number UNIQUE,
    FirstName       nvarchar(50)   NOT NULL,
    LastName        nvarchar(50)   NOT NULL,
    Email           nvarchar(120)  NULL,
    Phone           varchar(20)    NULL,
    CustomerSegment varchar(5)     NOT NULL,      -- RES or SMB
    CreatedAt       datetime2(0)   NOT NULL,      -- local time
    ModifiedAt      datetime2(0)   NOT NULL       -- local time
);

CREATE TABLE cis.Premise (
    PremiseId       int            NOT NULL CONSTRAINT PK_Premise PRIMARY KEY,
    StreetAddress   nvarchar(120)  NOT NULL,
    City            nvarchar(60)   NOT NULL,
    Province        char(2)        NOT NULL,
    PostalCode      varchar(7)     NOT NULL,
    PremiseType     varchar(20)    NOT NULL,      -- House, Townhouse, Apartment, Retail, Office
    CreatedAt       datetime2(0)   NOT NULL,
    ModifiedAt      datetime2(0)   NOT NULL
);

CREATE TABLE cis.ServiceAccount (
    AccountId       int            NOT NULL CONSTRAINT PK_ServiceAccount PRIMARY KEY,
    AccountNumber   varchar(12)    NOT NULL CONSTRAINT UQ_ServiceAccount_Number UNIQUE,
    CustomerId      int            NOT NULL CONSTRAINT FK_ServiceAccount_Customer REFERENCES cis.Customer (CustomerId),
    PremiseId       int            NOT NULL CONSTRAINT FK_ServiceAccount_Premise REFERENCES cis.Premise (PremiseId),
    BillingCycle    varchar(4)     NOT NULL,      -- BC01..BC04
    AccountStatus   varchar(10)    NOT NULL,      -- Active or Closed
    StartDate       date           NOT NULL,      -- first local date the account is responsible
    EndDate         date           NULL,          -- last local date, inclusive
    CreatedAt       datetime2(0)   NOT NULL,
    ModifiedAt      datetime2(0)   NOT NULL
);

CREATE TABLE cis.AccountRatePlan (
    AccountRatePlanId int          NOT NULL CONSTRAINT PK_AccountRatePlan PRIMARY KEY,
    AccountId       int            NOT NULL CONSTRAINT FK_AccountRatePlan_Account REFERENCES cis.ServiceAccount (AccountId),
    RatePlanCode    varchar(10)    NOT NULL CONSTRAINT FK_AccountRatePlan_RatePlan REFERENCES cis.RatePlan (RatePlanCode),
    EffectiveFrom   date           NOT NULL,
    EffectiveTo     date           NULL,          -- inclusive; NULL = current
    CreatedAt       datetime2(0)   NOT NULL,
    ModifiedAt      datetime2(0)   NOT NULL
);

/* ---------- meters ---------- */
CREATE TABLE cis.Meter (
    MeterId         int            NOT NULL CONSTRAINT PK_Meter PRIMARY KEY,
    MeterNumber     varchar(10)    NOT NULL CONSTRAINT UQ_Meter_Number UNIQUE,
    HeadEndCode     varchar(10)    NOT NULL,      -- NORTH, LAKE, RIDGE
    Manufacturer    nvarchar(60)   NOT NULL,
    Model           varchar(20)    NOT NULL,
    Multiplier      smallint       NOT NULL,      -- applies to NORTH register reads
    CreatedAt       datetime2(0)   NOT NULL,
    ModifiedAt      datetime2(0)   NOT NULL
);

CREATE TABLE cis.MeterInstall (
    MeterInstallId  int            NOT NULL CONSTRAINT PK_MeterInstall PRIMARY KEY,
    MeterId         int            NOT NULL CONSTRAINT FK_MeterInstall_Meter REFERENCES cis.Meter (MeterId),
    PremiseId       int            NOT NULL CONSTRAINT FK_MeterInstall_Premise REFERENCES cis.Premise (PremiseId),
    InstalledAt     datetime2(0)   NOT NULL,      -- local time, on the hour
    RemovedAt       datetime2(0)   NULL,          -- local time; NULL = still installed
    InitialRegisterKwh decimal(12,3) NULL,        -- register reading at install (NORTH only)
    CreatedAt       datetime2(0)   NOT NULL,
    ModifiedAt      datetime2(0)   NOT NULL
);
CREATE INDEX IX_MeterInstall_Premise ON cis.MeterInstall (PremiseId, InstalledAt);
CREATE INDEX IX_ServiceAccount_Premise ON cis.ServiceAccount (PremiseId, StartDate);
CREATE INDEX IX_AccountRatePlan_Account ON cis.AccountRatePlan (AccountId, EffectiveFrom);

/* ---------- simulator bookkeeping (not part of the "real" CIS) ---------- */
CREATE TABLE cis.SimulatorLog (
    ChangeDate      date           NOT NULL CONSTRAINT PK_SimulatorLog PRIMARY KEY,
    Note            nvarchar(200)  NULL,
    AppliedAt       datetime2(0)   NOT NULL CONSTRAINT DF_SimulatorLog_AppliedAt DEFAULT (SYSDATETIME())
);
GO

/* ---------- Change Tracking on every table the ETL extracts ---------- */
ALTER TABLE cis.RatePlan        ENABLE CHANGE_TRACKING WITH (TRACK_COLUMNS_UPDATED = ON);
ALTER TABLE cis.Season          ENABLE CHANGE_TRACKING WITH (TRACK_COLUMNS_UPDATED = ON);
ALTER TABLE cis.Holiday         ENABLE CHANGE_TRACKING WITH (TRACK_COLUMNS_UPDATED = ON);
ALTER TABLE cis.RatePrice       ENABLE CHANGE_TRACKING WITH (TRACK_COLUMNS_UPDATED = ON);
ALTER TABLE cis.TouWindow       ENABLE CHANGE_TRACKING WITH (TRACK_COLUMNS_UPDATED = ON);
ALTER TABLE cis.Customer        ENABLE CHANGE_TRACKING WITH (TRACK_COLUMNS_UPDATED = ON);
ALTER TABLE cis.Premise         ENABLE CHANGE_TRACKING WITH (TRACK_COLUMNS_UPDATED = ON);
ALTER TABLE cis.ServiceAccount  ENABLE CHANGE_TRACKING WITH (TRACK_COLUMNS_UPDATED = ON);
ALTER TABLE cis.AccountRatePlan ENABLE CHANGE_TRACKING WITH (TRACK_COLUMNS_UPDATED = ON);
ALTER TABLE cis.Meter           ENABLE CHANGE_TRACKING WITH (TRACK_COLUMNS_UPDATED = ON);
ALTER TABLE cis.MeterInstall    ENABLE CHANGE_TRACKING WITH (TRACK_COLUMNS_UPDATED = ON);
GO

PRINT 'TVH_CIS created. Next: run simulator/output/cis_seed/load_seed.sql';
GO
