-- ---------------------------------------------------------------------------
-- AB-Verified: the demo fixture (FR-154, FR-156, SPEC.md §10.1)
--
-- A repeatable seed producing verified and unverified organisations, jobs at
-- every lifecycle state, live invitations and bids awaiting release. It is the
-- same fixture as app/demo/fixture.py, row for row: the identifiers are the
-- same uuid5 values, the ABNs pass the same modulus-89 check (FR-104), and the
-- counts are the ones §10.1 promises.
--
--   Demo Client (Bayside Health Services)     1 job with released bids,
--                                             1 in bidding, 1 pending
--                                             approval, 1 draft
--   Demo Contractor, invited (Meridian)       1 open invitation, 1 accepted
--                                             invitation with a bid in progress
--   Demo Contractor, not invited (Southern    nothing at all, which is the
--   Cross Digital)                            point: it is the clearest
--                                             demonstration of the RLS boundary
--   Demo Staff                                7 verification cases including a
--                                             name mismatch, a duplicate ABN
--                                             and a cancelled ABN; 4 jobs to
--                                             moderate; 11 bids to release;
--                                             2 awards to confirm
--
-- Every row sets is_demo = true. That is how reset finds them again (FR-156)
-- and how scripts/check_no_demo_rows.py finds them if they ever reach
-- production (FR-158).
--
-- Instants are written relative to now(), so the fixture reads as current
-- however long after it was written the seed is run: an invitation that should
-- still be open is still open, and one that should have lapsed has lapsed.
--
-- Credentials are not here. Under Supabase Auth the four demo personas are
-- users in auth.users, created with these same identifiers by the step in
-- supabase/README.md. The password_hash column stays empty (NFR-04).
--
-- Idempotent: it deletes every demo row before inserting, so running it twice
-- leaves the same fixture, which is exactly what reset does (FR-156, §10.2).
-- ---------------------------------------------------------------------------

begin;

-- The seed runs as the migration owner. Row Level Security is forced on every
-- table (§9.2), so the owner is subject to it too, and the audit triggers
-- would write a second, redundant history over the one the fixture writes for
-- itself. Both are lifted for the duration of this transaction only. ALTER
-- TABLE is transactional, so a failure anywhere below rolls the tables back to
-- forced and audited rather than leaving them open.

alter table public.organisation disable trigger user;
alter table public.verification_case disable trigger user;
alter table public.verification_decision disable trigger user;
alter table public.job disable trigger user;
alter table public.invitation disable trigger user;
alter table public.bid disable trigger user;
alter table public.award disable trigger user;
alter table public.document disable trigger user;

alter table public.audit_event disable row level security;
alter table public.notification disable row level security;
alter table public.queue_message disable row level security;
alter table public.bid_version disable row level security;
alter table public.award disable row level security;
alter table public.bid disable row level security;
alter table public.invitation disable row level security;
alter table public.job disable row level security;
alter table public.verification_decision disable row level security;
alter table public.verification_case disable row level security;
alter table public.document disable row level security;
alter table public.contact_token disable row level security;
alter table public.abn_record disable row level security;
alter table public.user_profile disable row level security;
alter table public.organisation disable row level security;

-- ---------------------------------------------------------------------------
-- FR-156: delete only rows where is_demo, children before parents.
-- ---------------------------------------------------------------------------
delete from public.audit_event where is_demo;
delete from public.notification where is_demo;
delete from public.queue_message where is_demo;
delete from public.bid_version where is_demo;
delete from public.award where is_demo;
delete from public.bid where is_demo;
delete from public.invitation where is_demo;
delete from public.job where is_demo;
delete from public.verification_decision where is_demo;
delete from public.verification_case where is_demo;
delete from public.document where is_demo;
delete from public.contact_token where is_demo;
delete from public.abn_record where is_demo;
delete from public.user_profile where is_demo;
delete from public.organisation where is_demo;

-- ---------------------------------------------------------------------------
-- The fixture.
-- ---------------------------------------------------------------------------

-- organisation: 19 rows.
insert into public.organisation (
    id,
    legal_name,
    trading_name,
    kind,
    status,
    skills,
    categories,
    region,
    suspend_reason,
    is_demo,
    deleted_at,
    created_at
) values
    ('8efa56b1-0421-5bd7-8b5d-f8775c979155', 'Bayside Health Services Pty Ltd', null, 'CLIENT', 'VERIFIED', '[]', '["Healthcare"]', 'NSW', null, true, null, now() - interval '90 days'),
    ('4d13203b-681f-5576-bd09-f113071e4dcd', 'Nullarbor Freight Pty Ltd', null, 'CLIENT', 'VERIFIED', '[]', '["Transport and logistics"]', 'SA', null, true, null, now() - interval '90 days'),
    ('b96d47a6-0baa-5ce8-bfe6-b1c37b923d31', 'Meridian Cloud Works Pty Ltd', null, 'CONTRACTOR', 'VERIFIED', '["Microsoft 365","Exchange","Azure","Identity"]', '["Cloud"]', 'NSW', null, true, null, now() - interval '90 days'),
    ('b2fd7435-f172-501f-ba63-213785f3b4c3', 'Southern Cross Digital Pty Ltd', null, 'CONTRACTOR', 'VERIFIED', '["Web","Integration","Data"]', '["Software"]', 'VIC', null, true, null, now() - interval '90 days'),
    ('8f9052d0-c609-5ec7-a763-886f7b027d75', 'Blackwattle Systems Pty Ltd', null, 'CONTRACTOR', 'VERIFIED', '["Microsoft 365","Azure","Identity"]', '["IT services"]', 'NSW', null, true, null, now() - interval '90 days'),
    ('25065662-7411-554f-83a0-8ea03cd19228', 'Torrens Data Group Pty Ltd', null, 'CONTRACTOR', 'VERIFIED', '["Data platform","Power BI","Integration"]', '["IT services"]', 'SA', null, true, null, now() - interval '90 days'),
    ('0690df46-4876-5e49-840d-60ea11454e04', 'Kangaroo Point Networks Pty Ltd', null, 'CONTRACTOR', 'VERIFIED', '["Networking","Wireless","Meraki"]', '["IT services"]', 'QLD', null, true, null, now() - interval '90 days'),
    ('f6607090-6b6b-56c1-bc8c-1076fae6bf09', 'Fremantle Secure Pty Ltd', null, 'CONTRACTOR', 'VERIFIED', '["Security","Penetration testing","Essential Eight"]', '["IT services"]', 'WA', null, true, null, now() - interval '90 days'),
    ('f4bfa491-3bf2-5dd8-bffd-fb9a549f0619', 'Yarra Endpoint Services Pty Ltd', null, 'CONTRACTOR', 'VERIFIED', '["Endpoint","Intune","SOE"]', '["IT services"]', 'VIC', null, true, null, now() - interval '90 days'),
    ('98a9648c-30e0-50e1-b8b0-e8079d7f9a13', 'Adelaide Integration Partners Pty Ltd', null, 'CONTRACTOR', 'VERIFIED', '["Integration","Payroll","APIs"]', '["IT services"]', 'SA', null, true, null, now() - interval '90 days'),
    ('e4c33d44-0eeb-547d-880f-9b6fd6eca987', 'Hobart Managed IT Pty Ltd', null, 'CONTRACTOR', 'VERIFIED', '["Service desk","Managed services","ITIL"]', '["IT services"]', 'TAS', null, true, null, now() - interval '90 days'),
    ('2378aa43-06b4-524a-aa56-c020741fd473', 'Darwin Network Co Pty Ltd', null, 'CONTRACTOR', 'VERIFIED', '["Networking","Cabling","Field services"]', '["IT services"]', 'NT', null, true, null, now() - interval '90 days'),
    ('0d6b620b-2c69-50ab-b8ee-019980d4f2ae', 'Bunya Networks Pty Ltd', null, 'CONTRACTOR', 'PENDING', '[]', '[]', 'QLD', null, true, null, now() - interval '6 days'),
    ('de48707c-e8b0-51c3-aa14-9d7e5c6dea79', 'Warrigal IT Solutions', null, 'CONTRACTOR', 'PENDING', '[]', '[]', 'NSW', null, true, null, now() - interval '6 days'),
    ('715793e0-91a0-5d70-b5d4-6b8910dcda61', 'Coolabah Technologies Pty Ltd', null, 'CONTRACTOR', 'PENDING', '[]', '[]', 'VIC', null, true, null, now() - interval '6 days'),
    ('2f6f27af-1472-5ead-82dd-2d273680ca8b', 'Derwent Systems Pty Ltd', null, 'CONTRACTOR', 'PENDING', '[]', '[]', 'TAS', null, true, null, now() - interval '6 days'),
    ('a8bda071-88de-5b4b-9479-ff326f84d000', 'Pilbara Cyber Pty Ltd', null, 'CONTRACTOR', 'PENDING', '[]', '[]', 'WA', null, true, null, now() - interval '6 days'),
    ('69456d0c-3f1e-59cd-aa63-1240746ade56', 'Gippsland Managed Services Pty Ltd', null, 'CONTRACTOR', 'PENDING', '[]', '[]', 'VIC', null, true, null, now() - interval '6 days'),
    ('777080af-ba57-5bde-a0a6-3b2eae352d33', 'Moreton Bay Digital Pty Ltd', null, 'CLIENT', 'PENDING', '[]', '[]', 'QLD', null, true, null, now() - interval '6 days');

-- abn_record: 19 rows.
insert into public.abn_record (
    id,
    organisation_id,
    abn,
    abr_entity_name,
    abr_entity_type,
    abr_status,
    gst_registered,
    lookup_state,
    raw_response,
    checked_at,
    is_demo
) values
    ('5cb3e6e6-e83a-5746-946a-8336413f3438', '8efa56b1-0421-5bd7-8b5d-f8775c979155', '63901624888', 'BAYSIDE HEALTH SERVICES PTY LTD', 'Australian Private Company', 'ACTIVE', true, 'OK', '{"abn":"63901624888","entityName":"BAYSIDE HEALTH SERVICES PTY LTD","entityStatus":"ACTIVE","gstRegistered":true,"source":"ABR ABN Lookup (demo fixture)"}', now() - interval '2 days', true),
    ('87512a80-1877-5ee4-a32e-fa6d32f86fff', '4d13203b-681f-5576-bd09-f113071e4dcd', '98330102783', 'NULLARBOR FREIGHT PTY LTD', 'Australian Private Company', 'ACTIVE', true, 'OK', '{"abn":"98330102783","entityName":"NULLARBOR FREIGHT PTY LTD","entityStatus":"ACTIVE","gstRegistered":true,"source":"ABR ABN Lookup (demo fixture)"}', now() - interval '2 days', true),
    ('466d2402-9c1a-5380-bf71-8e46b774ef3c', 'b96d47a6-0baa-5ce8-bfe6-b1c37b923d31', '57616145724', 'MERIDIAN CLOUD WORKS PTY LTD', 'Australian Private Company', 'ACTIVE', true, 'OK', '{"abn":"57616145724","entityName":"MERIDIAN CLOUD WORKS PTY LTD","entityStatus":"ACTIVE","gstRegistered":true,"source":"ABR ABN Lookup (demo fixture)"}', now() - interval '2 days', true),
    ('3c1e4731-b2b2-5006-9977-34924d158608', 'b2fd7435-f172-501f-ba63-213785f3b4c3', '71169951752', 'SOUTHERN CROSS DIGITAL PTY LTD', 'Australian Private Company', 'ACTIVE', true, 'OK', '{"abn":"71169951752","entityName":"SOUTHERN CROSS DIGITAL PTY LTD","entityStatus":"ACTIVE","gstRegistered":true,"source":"ABR ABN Lookup (demo fixture)"}', now() - interval '2 days', true),
    ('e8e8bf29-d5eb-5b28-a175-7cd9b31debec', '8f9052d0-c609-5ec7-a763-886f7b027d75', '79083798508', 'BLACKWATTLE SYSTEMS PTY LTD', 'Australian Private Company', 'ACTIVE', true, 'OK', '{"abn":"79083798508","entityName":"BLACKWATTLE SYSTEMS PTY LTD","entityStatus":"ACTIVE","gstRegistered":true,"source":"ABR ABN Lookup (demo fixture)"}', now() - interval '2 days', true),
    ('ff5eddca-d860-5d5b-b02b-6c7c1649d6d6', '25065662-7411-554f-83a0-8ea03cd19228', '14624862422', 'TORRENS DATA GROUP PTY LTD', 'Australian Private Company', 'ACTIVE', true, 'OK', '{"abn":"14624862422","entityName":"TORRENS DATA GROUP PTY LTD","entityStatus":"ACTIVE","gstRegistered":true,"source":"ABR ABN Lookup (demo fixture)"}', now() - interval '2 days', true),
    ('9e8578ae-6b08-558e-8dde-b118bc8bcc5a', '0690df46-4876-5e49-840d-60ea11454e04', '78409326682', 'KANGAROO POINT NETWORKS PTY LTD', 'Australian Private Company', 'ACTIVE', true, 'OK', '{"abn":"78409326682","entityName":"KANGAROO POINT NETWORKS PTY LTD","entityStatus":"ACTIVE","gstRegistered":true,"source":"ABR ABN Lookup (demo fixture)"}', now() - interval '2 days', true),
    ('3ef15478-9518-58ee-bc27-35a5e282b36f', 'f6607090-6b6b-56c1-bc8c-1076fae6bf09', '47751147887', 'FREMANTLE SECURE PTY LTD', 'Australian Private Company', 'ACTIVE', true, 'OK', '{"abn":"47751147887","entityName":"FREMANTLE SECURE PTY LTD","entityStatus":"ACTIVE","gstRegistered":true,"source":"ABR ABN Lookup (demo fixture)"}', now() - interval '2 days', true),
    ('47f3d030-0372-59a2-a5b7-3b393d13a8b5', 'f4bfa491-3bf2-5dd8-bffd-fb9a549f0619', '39764316341', 'YARRA ENDPOINT SERVICES PTY LTD', 'Australian Private Company', 'ACTIVE', true, 'OK', '{"abn":"39764316341","entityName":"YARRA ENDPOINT SERVICES PTY LTD","entityStatus":"ACTIVE","gstRegistered":true,"source":"ABR ABN Lookup (demo fixture)"}', now() - interval '2 days', true),
    ('37d317cf-f01f-5e1e-bab8-a588ab8a2860', '98a9648c-30e0-50e1-b8b0-e8079d7f9a13', '36743120992', 'ADELAIDE INTEGRATION PARTNERS PTY LTD', 'Australian Private Company', 'ACTIVE', true, 'OK', '{"abn":"36743120992","entityName":"ADELAIDE INTEGRATION PARTNERS PTY LTD","entityStatus":"ACTIVE","gstRegistered":true,"source":"ABR ABN Lookup (demo fixture)"}', now() - interval '2 days', true),
    ('31dee07a-5ef1-5e2b-ae52-d24b32c5bec9', 'e4c33d44-0eeb-547d-880f-9b6fd6eca987', '70974103753', 'HOBART MANAGED IT PTY LTD', 'Australian Private Company', 'ACTIVE', true, 'OK', '{"abn":"70974103753","entityName":"HOBART MANAGED IT PTY LTD","entityStatus":"ACTIVE","gstRegistered":true,"source":"ABR ABN Lookup (demo fixture)"}', now() - interval '2 days', true),
    ('6ffea25d-7262-5220-b7da-da9ea9ef90bf', '2378aa43-06b4-524a-aa56-c020741fd473', '26249761471', 'DARWIN NETWORK CO PTY LTD', 'Australian Private Company', 'ACTIVE', true, 'OK', '{"abn":"26249761471","entityName":"DARWIN NETWORK CO PTY LTD","entityStatus":"ACTIVE","gstRegistered":true,"source":"ABR ABN Lookup (demo fixture)"}', now() - interval '2 days', true),
    ('fbe14ec2-e0fe-5d01-8940-27a0448ffd77', '0d6b620b-2c69-50ab-b8ee-019980d4f2ae', '88012582263', 'BUNYA NETWORKS PTY LTD', 'Australian Private Company', 'ACTIVE', true, 'OK', '{"abn":"88012582263","entityName":"BUNYA NETWORKS PTY LTD","entityStatus":"ACTIVE","gstRegistered":true,"source":"ABR ABN Lookup (demo fixture)"}', now() - interval '2 days', true),
    ('2ed1190f-b466-520c-a0ee-a6950940b828', 'de48707c-e8b0-51c3-aa14-9d7e5c6dea79', '85408198696', 'M J HOLDINGS (AUST) PTY LTD', 'Australian Private Company', 'ACTIVE', true, 'OK', '{"abn":"85408198696","entityName":"M J HOLDINGS (AUST) PTY LTD","entityStatus":"ACTIVE","gstRegistered":true,"source":"ABR ABN Lookup (demo fixture)"}', now() - interval '2 days', true),
    ('4828d3c5-fe54-5a91-b757-b71db7309274', '715793e0-91a0-5d70-b5d4-6b8910dcda61', '79427090548', 'COOLABAH TECHNOLOGIES PTY LTD', 'Australian Private Company', 'ACTIVE', true, 'OK', '{"abn":"79427090548","entityName":"COOLABAH TECHNOLOGIES PTY LTD","entityStatus":"ACTIVE","gstRegistered":true,"source":"ABR ABN Lookup (demo fixture)"}', now() - interval '2 days', true),
    ('81ff8c63-0495-5bd7-8b2d-49640b68ada3', '2f6f27af-1472-5ead-82dd-2d273680ca8b', '67606017955', 'DERWENT SYSTEMS PTY LTD', 'Australian Private Company', 'CANCELLED', true, 'OK', '{"abn":"67606017955","entityName":"DERWENT SYSTEMS PTY LTD","entityStatus":"CANCELLED","gstRegistered":true,"source":"ABR ABN Lookup (demo fixture)"}', now() - interval '2 days', true),
    ('aba211fc-64d8-5ae3-af28-bf17bc355dc3', 'a8bda071-88de-5b4b-9479-ff326f84d000', '30340596163', null, 'Australian Private Company', 'UNKNOWN', false, 'ABR_UNAVAILABLE', '{"abn":"30340596163","entityName":"","entityStatus":"UNKNOWN","gstRegistered":false,"source":"ABR ABN Lookup (demo fixture)"}', now() - interval '2 days', true),
    ('54bb4438-9043-588f-b9ec-1cd56f8151e0', '69456d0c-3f1e-59cd-aa63-1240746ade56', '75524983264', 'GIPPSLAND MANAGED SERVICES PTY LTD', 'Australian Private Company', 'ACTIVE', true, 'OK', '{"abn":"75524983264","entityName":"GIPPSLAND MANAGED SERVICES PTY LTD","entityStatus":"ACTIVE","gstRegistered":true,"source":"ABR ABN Lookup (demo fixture)"}', now() - interval '2 days', true),
    ('98ed2169-ff67-569d-8fad-9daaa5b5edd5', '777080af-ba57-5bde-a0a6-3b2eae352d33', '23391452117', 'MORETON BAY DIGITAL PTY LTD', 'Australian Private Company', 'ACTIVE', true, 'OK', '{"abn":"23391452117","entityName":"MORETON BAY DIGITAL PTY LTD","entityStatus":"ACTIVE","gstRegistered":true,"source":"ABR ABN Lookup (demo fixture)"}', now() - interval '2 days', true);

-- user_profile: 21 rows.
insert into public.user_profile (
    id,
    organisation_id,
    email,
    full_name,
    mobile_e164,
    role,
    password_hash,
    email_verified,
    mobile_verified,
    mfa_enrolled,
    is_demo,
    deleted_at,
    created_at
) values
    ('d3659304-b61d-584f-9645-7d44924c67c2', null, 'staff@demo.ab-verified.invalid', 'Jordan Mills', '+61400000000', 'STAFF', '', true, true, true, true, null, now() - interval '88 days'),
    ('4f71864f-1d3c-5f73-913a-95c82e19db48', null, 'staff2@demo.ab-verified.invalid', 'Wei Chen', '+61400000000', 'STAFF', '', true, true, true, true, null, now() - interval '88 days'),
    ('223396be-ddff-5ea2-8ef8-9c9015f07410', '8efa56b1-0421-5bd7-8b5d-f8775c979155', 'client@demo.ab-verified.invalid', 'Priya Raman', '+61400000000', 'CLIENT', '', true, true, false, true, null, now() - interval '88 days'),
    ('6c9934d6-6586-51c3-b595-83cb7d2606a8', '4d13203b-681f-5576-bd09-f113071e4dcd', 'logistics@demo.ab-verified.invalid', 'Dan Whitely', '+61400000000', 'CLIENT', '', true, true, false, true, null, now() - interval '88 days'),
    ('19eb3f61-4c34-53d2-9b0d-0644998340e5', 'b96d47a6-0baa-5ce8-bfe6-b1c37b923d31', 'contractor@demo.ab-verified.invalid', 'Tom Okafor', '+61400000000', 'CONTRACTOR', '', true, true, false, true, null, now() - interval '88 days'),
    ('582c2109-fdf4-5f82-beba-e1e9271331f6', 'b2fd7435-f172-501f-ba63-213785f3b4c3', 'outsider@demo.ab-verified.invalid', 'Alice Nguyen', '+61400000000', 'CONTRACTOR', '', true, true, false, true, null, now() - interval '88 days'),
    ('2f3e8c56-48c0-5882-a9b0-b1ec14f5599d', '8f9052d0-c609-5ec7-a763-886f7b027d75', 'blackwattle@demo.ab-verified.invalid', 'Blackwattle Systems', '+61400000000', 'CONTRACTOR', '', true, true, false, true, null, now() - interval '88 days'),
    ('2c156d92-1e69-5a8e-86c1-ff3b7bfc43d4', '25065662-7411-554f-83a0-8ea03cd19228', 'torrens@demo.ab-verified.invalid', 'Torrens Data Group', '+61400000000', 'CONTRACTOR', '', true, true, false, true, null, now() - interval '88 days'),
    ('472c3cf0-1abf-55c7-99cb-825e23303e4f', '0690df46-4876-5e49-840d-60ea11454e04', 'kangaroopoint@demo.ab-verified.invalid', 'Kangaroo Point Networks', '+61400000000', 'CONTRACTOR', '', true, true, false, true, null, now() - interval '88 days'),
    ('2f4ec10b-b96e-52eb-90e5-577eb92dc896', 'f6607090-6b6b-56c1-bc8c-1076fae6bf09', 'fremantle@demo.ab-verified.invalid', 'Fremantle Secure', '+61400000000', 'CONTRACTOR', '', true, true, false, true, null, now() - interval '88 days'),
    ('b7cac4a4-b4e7-5e60-8c71-060a38c5b566', 'f4bfa491-3bf2-5dd8-bffd-fb9a549f0619', 'yarra@demo.ab-verified.invalid', 'Yarra Endpoint Services', '+61400000000', 'CONTRACTOR', '', true, true, false, true, null, now() - interval '88 days'),
    ('c2480b65-f8c3-5f70-bafd-8395fa0733a1', '98a9648c-30e0-50e1-b8b0-e8079d7f9a13', 'adelaideint@demo.ab-verified.invalid', 'Adelaide Integration Partners', '+61400000000', 'CONTRACTOR', '', true, true, false, true, null, now() - interval '88 days'),
    ('3216696f-88e0-59f6-af61-90178dcf90fb', 'e4c33d44-0eeb-547d-880f-9b6fd6eca987', 'hobart@demo.ab-verified.invalid', 'Hobart Managed IT', '+61400000000', 'CONTRACTOR', '', true, true, false, true, null, now() - interval '88 days'),
    ('01dca114-7728-5b7b-9faf-75ec915afa77', '2378aa43-06b4-524a-aa56-c020741fd473', 'darwinnet@demo.ab-verified.invalid', 'Darwin Network Co', '+61400000000', 'CONTRACTOR', '', true, true, false, true, null, now() - interval '88 days'),
    ('f97d5533-30dd-532e-ac91-12ebe2dd60ac', '0d6b620b-2c69-50ab-b8ee-019980d4f2ae', 'bunya@demo.ab-verified.invalid', 'Bunya Networks', '+61400000000', 'CONTRACTOR', '', true, true, false, true, null, now() - interval '88 days'),
    ('c56ef9c1-9f79-556f-821a-398add26d51b', 'de48707c-e8b0-51c3-aa14-9d7e5c6dea79', 'warrigal@demo.ab-verified.invalid', 'Warrigal IT Solutions', '+61400000000', 'CONTRACTOR', '', true, true, false, true, null, now() - interval '88 days'),
    ('6b932c1c-ec02-51db-9585-2de265475909', '715793e0-91a0-5d70-b5d4-6b8910dcda61', 'coolabah@demo.ab-verified.invalid', 'Coolabah Technologies', '+61400000000', 'CONTRACTOR', '', true, true, false, true, null, now() - interval '88 days'),
    ('9daa7454-0dbe-544e-be4f-0d7374ae9393', '2f6f27af-1472-5ead-82dd-2d273680ca8b', 'derwent@demo.ab-verified.invalid', 'Derwent Systems', '+61400000000', 'CONTRACTOR', '', true, true, false, true, null, now() - interval '88 days'),
    ('a42045a6-403a-57ed-ad3b-d443022e961f', 'a8bda071-88de-5b4b-9479-ff326f84d000', 'pilbara@demo.ab-verified.invalid', 'Pilbara Cyber', '+61400000000', 'CONTRACTOR', '', true, true, false, true, null, now() - interval '88 days'),
    ('4741d31d-13c5-5bd2-b3f2-511a495839dd', '69456d0c-3f1e-59cd-aa63-1240746ade56', 'gippsland@demo.ab-verified.invalid', 'Gippsland Managed Services', '+61400000000', 'CONTRACTOR', '', true, true, false, true, null, now() - interval '88 days'),
    ('950fdabc-2f9c-5612-b223-bfb1629913c9', '777080af-ba57-5bde-a0a6-3b2eae352d33', 'moretonbay@demo.ab-verified.invalid', 'Moreton Bay Digital', '+61400000000', 'CLIENT', '', true, true, false, true, null, now() - interval '88 days');

-- contact_token: no rows in the fixture.

-- verification_case: 19 rows.
insert into public.verification_case (
    id,
    organisation_id,
    state,
    name_match_score,
    duplicate_abn_flag,
    assigned_staff_id,
    is_demo,
    opened_at,
    closed_at
) values
    ('9f24cb27-c8a3-5e3d-861c-ac2391f7e414', '8efa56b1-0421-5bd7-8b5d-f8775c979155', 'APPROVED', 100, false, 'd3659304-b61d-584f-9645-7d44924c67c2', true, now() - interval '60 days', now() - interval '59 days'),
    ('05b7d4be-142c-5a2b-a11b-fab3ac7b3a34', '4d13203b-681f-5576-bd09-f113071e4dcd', 'APPROVED', 100, false, 'd3659304-b61d-584f-9645-7d44924c67c2', true, now() - interval '70 days', now() - interval '69 days'),
    ('913a70f7-2a6e-5ac8-a622-ed3ba1c05722', 'b96d47a6-0baa-5ce8-bfe6-b1c37b923d31', 'APPROVED', 100, false, 'd3659304-b61d-584f-9645-7d44924c67c2', true, now() - interval '55 days', now() - interval '54 days'),
    ('f7cabf9f-381d-540f-85f6-9202a55ed1f3', 'b2fd7435-f172-501f-ba63-213785f3b4c3', 'APPROVED', 100, false, 'd3659304-b61d-584f-9645-7d44924c67c2', true, now() - interval '50 days', now() - interval '49 days'),
    ('240a90f6-b6ad-5a01-8612-cc07f2bfacb6', '8f9052d0-c609-5ec7-a763-886f7b027d75', 'APPROVED', 99, false, 'd3659304-b61d-584f-9645-7d44924c67c2', true, now() - interval '45 days', now() - interval '44 days'),
    ('bb9449d5-dfe1-50fa-8288-5c6e4e6e68e5', '25065662-7411-554f-83a0-8ea03cd19228', 'APPROVED', 99, false, 'd3659304-b61d-584f-9645-7d44924c67c2', true, now() - interval '45 days', now() - interval '44 days'),
    ('5c7070bb-85ee-54f7-bde7-0007c2f0c876', '0690df46-4876-5e49-840d-60ea11454e04', 'APPROVED', 99, false, 'd3659304-b61d-584f-9645-7d44924c67c2', true, now() - interval '45 days', now() - interval '44 days'),
    ('ee2b8ab8-bb9d-5ca9-ac4c-2e8b56f3e231', 'f6607090-6b6b-56c1-bc8c-1076fae6bf09', 'APPROVED', 99, false, 'd3659304-b61d-584f-9645-7d44924c67c2', true, now() - interval '45 days', now() - interval '44 days'),
    ('14350f2e-87ba-510b-97ee-029ac0dbe2d4', 'f4bfa491-3bf2-5dd8-bffd-fb9a549f0619', 'APPROVED', 99, false, 'd3659304-b61d-584f-9645-7d44924c67c2', true, now() - interval '45 days', now() - interval '44 days'),
    ('39455091-d540-5522-ba42-cfbfbf213e91', '98a9648c-30e0-50e1-b8b0-e8079d7f9a13', 'APPROVED', 99, false, 'd3659304-b61d-584f-9645-7d44924c67c2', true, now() - interval '45 days', now() - interval '44 days'),
    ('b686c016-cb65-5a97-b725-1be6754cd27b', 'e4c33d44-0eeb-547d-880f-9b6fd6eca987', 'APPROVED', 99, false, 'd3659304-b61d-584f-9645-7d44924c67c2', true, now() - interval '45 days', now() - interval '44 days'),
    ('ccd816fd-b783-5092-b3ed-b5fc7a6a5d42', '2378aa43-06b4-524a-aa56-c020741fd473', 'APPROVED', 99, false, 'd3659304-b61d-584f-9645-7d44924c67c2', true, now() - interval '45 days', now() - interval '44 days'),
    ('71faa531-1922-5a9b-b275-db1243c64dd6', '0d6b620b-2c69-50ab-b8ee-019980d4f2ae', 'IN_REVIEW', 96, false, null, true, now() - interval '4 days', null),
    ('33385ed6-45c9-5beb-8723-a61208483b0b', 'de48707c-e8b0-51c3-aa14-9d7e5c6dea79', 'IN_REVIEW', 31, false, null, true, now() - interval '4 days', null),
    ('7f9c01e4-4689-58d6-87d1-57de4a9dcc18', '715793e0-91a0-5d70-b5d4-6b8910dcda61', 'IN_REVIEW', 98, true, null, true, now() - interval '4 days', null),
    ('d38754bf-81d3-5d75-a8e5-28a3e480d211', '2f6f27af-1472-5ead-82dd-2d273680ca8b', 'IN_REVIEW', 100, false, null, true, now() - interval '4 days', null),
    ('64eca603-8aad-5cc0-b55a-e9b1bac75b7c', 'a8bda071-88de-5b4b-9479-ff326f84d000', 'IN_REVIEW', 0, false, null, true, now() - interval '4 days', null),
    ('f241ec71-47a3-599e-b497-5a8b0fb94882', '69456d0c-3f1e-59cd-aa63-1240746ade56', 'IN_REVIEW', 100, false, null, true, now() - interval '4 days', null),
    ('cbb3568c-c7f5-5e77-a34f-a884add89196', '777080af-ba57-5bde-a0a6-3b2eae352d33', 'INFO_REQUESTED', 92, false, null, true, now() - interval '4 days', null);

-- verification_decision: 1 row.
insert into public.verification_decision (
    id,
    case_id,
    staff_user_id,
    outcome,
    reason_code,
    note_to_applicant,
    internal_note,
    superseded_by,
    is_demo,
    decided_at
) values
    ('74fe6eca-4f7a-5831-9ec4-aeb5a835b7c9', 'cbb3568c-c7f5-5e77-a34f-a884add89196', 'd3659304-b61d-584f-9645-7d44924c67c2', 'REQUEST_INFO', 'INSUFFICIENT_DOCUMENTS', 'Please upload a current certificate of currency for your professional indemnity insurance.', 'Waiting on PI cover before approving.', null, true, now() - interval '2 days');

-- document: 2 rows.
insert into public.document (
    id,
    organisation_id,
    kind,
    filename,
    content_type,
    size_bytes,
    storage_path,
    is_demo,
    uploaded_at
) values
    ('e08f430c-80fb-5ae3-ada3-b5ed3d939c14', '0d6b620b-2c69-50ab-b8ee-019980d4f2ae', 'Certificate of currency', 'bunya-certificate-of-currency.pdf', 'application/pdf', 214233, 'org/0d6b620b-2c69-50ab-b8ee-019980d4f2ae/certificate-of-currency.pdf', true, now() - interval '4 days'),
    ('13a38d62-d47d-5949-b8ba-2554b90f4a05', '69456d0c-3f1e-59cd-aa63-1240746ade56', 'Certificate of currency', 'gippsland-certificate-of-currency.pdf', 'application/pdf', 214233, 'org/69456d0c-3f1e-59cd-aa63-1240746ade56/certificate-of-currency.pdf', true, now() - interval '4 days');

-- job: 11 rows.
insert into public.job (
    id,
    client_org_id,
    title,
    description,
    category,
    required_skills,
    engagement_type,
    budget_min,
    budget_max,
    location,
    start_date,
    duration,
    state,
    reject_reason_code,
    staff_feedback,
    cancel_reason,
    contact_flagged,
    bids_close_at,
    is_demo,
    deleted_at,
    created_at,
    submitted_at
) values
    ('2ccca276-af89-53ed-a994-e6184f34c8a1', '8efa56b1-0421-5bd7-8b5d-f8775c979155', 'Migrate 40 staff from on-premise Exchange to Microsoft 365', 'We run Exchange 2016 on a single host at our Rozelle site and need to move 40 mailboxes to Microsoft 365, including shared mailboxes and two resource calendars. Mail flow must not be interrupted during clinic hours. We need MFA enforced on completion and a short handover session for our two internal IT staff.', 'Cloud migration', '["Microsoft 365","Exchange","Identity"]', 'FIXED', 18000.0, 32000.0, 'Sydney, NSW', (now() + interval '30 days')::date, '6 weeks', 'BIDS_RELEASED', null, null, null, false, '2026-05-30T00:00:00+00:00', true, null, now() - interval '34 days', now() - interval '33 days'),
    ('7d2cdb05-61b8-5ba3-be52-8ced7f05ba38', '8efa56b1-0421-5bd7-8b5d-f8775c979155', 'Replace ageing wireless across three clinics', 'Three clinic sites in inner west Sydney are running end-of-life access points. We need a like-for-like replacement with current hardware, a site survey at each location, and separate guest and clinical SSIDs. Work must be done outside clinic hours.', 'Networking', '["Networking","Wireless","Meraki"]', 'FIXED', 24000.0, 40000.0, 'Sydney, NSW', (now() + interval '30 days')::date, '6 weeks', 'BIDDING', null, null, null, false, '2026-06-06T00:00:00+00:00', true, null, now() - interval '18 days', now() - interval '17 days'),
    ('252283ec-647d-57e6-8863-86e5f0257d00', '8efa56b1-0421-5bd7-8b5d-f8775c979155', 'Annual penetration test of the patient portal', 'Grey-box penetration test of our patient booking portal and its API, with a report suitable for our board and a retest of remediated findings four weeks later. Testing must not touch production patient records.', 'Security', '["Penetration testing","Security","OWASP"]', 'FIXED', 12000.0, 18000.0, 'Remote', (now() + interval '30 days')::date, '6 weeks', 'PENDING_APPROVAL', null, null, null, false, null, true, null, now() - interval '3 days', now() - interval '2 days'),
    ('1190a4db-a1f0-5b7b-b4de-6db117154973', '8efa56b1-0421-5bd7-8b5d-f8775c979155', 'SOE rebuild for clinical workstations', 'Draft. We want to rebuild the standard operating environment for roughly 120 clinical workstations, moving to Intune management.', 'Endpoint', '["Intune","SOE","Endpoint"]', 'FIXED', 30000.0, 55000.0, 'Sydney, NSW', (now() + interval '30 days')::date, '6 weeks', 'DRAFT', null, null, null, false, null, true, null, now() - interval '2 days', null),
    ('7cba847f-4bc4-5953-9851-0c51505e0a50', '4d13203b-681f-5576-bd09-f113071e4dcd', 'Warehouse barcode scanner fleet refresh', 'Replace 90 ageing handheld scanners across two distribution centres, including the mobile device management enrolment and staff training.', 'Endpoint', '["Endpoint","Mobility","MDM"]', 'FIXED', 60000.0, 90000.0, 'Adelaide, SA', (now() + interval '30 days')::date, '6 weeks', 'PENDING_APPROVAL', null, null, null, false, null, true, null, now() - interval '2 days', now() - interval '1 days'),
    ('c701c175-732e-5526-8122-b4a39cad2e6f', '4d13203b-681f-5576-bd09-f113071e4dcd', 'Essential Eight uplift to maturity level two', 'Assessment against the Essential Eight, a remediation plan and hands-on uplift work to reach maturity level two across the corporate environment.', 'Security', '["Essential Eight","Security","Hardening"]', 'FIXED', 45000.0, 70000.0, 'Adelaide, SA', (now() + interval '30 days')::date, '6 weeks', 'PENDING_APPROVAL', null, null, null, false, null, true, null, now() - interval '1 days', now()),
    ('e6a70679-880d-54f7-90db-29a32185f80c', '4d13203b-681f-5576-bd09-f113071e4dcd', 'Server room decommission and data destruction', 'Decommission the Port Adelaide server room, with certified data destruction, asset register reconciliation and environmentally sound disposal.', 'Infrastructure', '["Infrastructure","Decommissioning","Asset management"]', 'FIXED', 15000.0, 25000.0, 'Adelaide, SA', (now() + interval '30 days')::date, '6 weeks', 'PENDING_APPROVAL', null, null, null, false, null, true, null, now() - interval '1 days', now()),
    ('80704f32-7c1c-5df3-891a-8bb6b0617c1f', '4d13203b-681f-5576-bd09-f113071e4dcd', 'Transition to a 24x7 managed service desk', 'Transition first and second level support for 400 staff to a managed service desk with 24x7 coverage, including knowledge transfer and an agreed set of service levels.', 'Managed services', '["Service desk","ITIL","Managed services"]', 'DAY_RATE', 1200.0, 1800.0, 'Adelaide, SA', (now() + interval '30 days')::date, '6 weeks', 'BIDS_CLOSED', null, null, null, false, '2026-05-31T00:00:00+00:00', true, null, now() - interval '40 days', now() - interval '39 days'),
    ('1d3809ad-4d20-5a70-9bba-5c1da8fde09c', '4d13203b-681f-5576-bd09-f113071e4dcd', 'Fleet telematics data warehouse', 'Consolidate telematics feeds from 300 vehicles into a warehouse with daily reporting on utilisation, idle time and fuel burn.', 'Data', '["Data platform","Power BI","Integration"]', 'FIXED', 70000.0, 110000.0, 'Remote', (now() + interval '30 days')::date, '6 weeks', 'AWARD_PENDING', null, null, null, false, '2026-05-26T00:00:00+00:00', true, null, now() - interval '55 days', now() - interval '54 days'),
    ('97e0380d-fa0d-5744-8fc3-5c6d9947d7d3', '4d13203b-681f-5576-bd09-f113071e4dcd', 'Payroll system integration', 'Integrate the new payroll platform with our rostering system and the finance ledger, including a parallel run over two pay cycles.', 'Integration', '["Integration","Payroll","APIs"]', 'FIXED', 40000.0, 65000.0, 'Adelaide, SA', (now() + interval '30 days')::date, '6 weeks', 'AWARD_PENDING', null, null, null, false, '2026-05-23T00:00:00+00:00', true, null, now() - interval '60 days', now() - interval '59 days'),
    ('f0f19452-a124-5184-9406-d5e8660f0a8b', '4d13203b-681f-5576-bd09-f113071e4dcd', 'Legacy AS/400 report migration', 'Rewrite 40 operational reports currently produced on an AS/400 into the new reporting platform, with output reconciled line for line.', 'Data', '["Data platform","Reporting","Migration"]', 'FIXED', 35000.0, 55000.0, 'Remote', (now() + interval '30 days')::date, '6 weeks', 'INVITING', null, null, null, false, '2026-06-10T00:00:00+00:00', true, null, now() - interval '10 days', now() - interval '9 days');

-- invitation: 23 rows.
insert into public.invitation (
    id,
    job_id,
    contractor_org_id,
    invited_by_staff_id,
    state,
    decline_reason,
    is_demo,
    sent_at,
    expires_at,
    responded_at
) values
    ('bf1df105-703a-5364-b95b-4f44e3e05045', 'f0f19452-a124-5184-9406-d5e8660f0a8b', 'b96d47a6-0baa-5ce8-bfe6-b1c37b923d31', 'd3659304-b61d-584f-9645-7d44924c67c2', 'SENT', null, true, now() - interval '2 days', now() + interval '9 days', null),
    ('15dba115-45ae-5531-a74b-248c713a3616', '7d2cdb05-61b8-5ba3-be52-8ced7f05ba38', 'b96d47a6-0baa-5ce8-bfe6-b1c37b923d31', 'd3659304-b61d-584f-9645-7d44924c67c2', 'ACCEPTED', null, true, now() - interval '8 days', now() + interval '5 days', now() - interval '7 days'),
    ('70a95579-80a4-5d0d-9f77-8d7e6c287370', '2ccca276-af89-53ed-a994-e6184f34c8a1', '8f9052d0-c609-5ec7-a763-886f7b027d75', 'd3659304-b61d-584f-9645-7d44924c67c2', 'ACCEPTED', null, true, now() - interval '25 days', now() - interval '2 days', now() - interval '24 days'),
    ('73ad08ff-28de-5822-8f6b-8a78881cb9d4', '2ccca276-af89-53ed-a994-e6184f34c8a1', '25065662-7411-554f-83a0-8ea03cd19228', 'd3659304-b61d-584f-9645-7d44924c67c2', 'ACCEPTED', null, true, now() - interval '25 days', now() - interval '2 days', now() - interval '24 days'),
    ('dc05f9c7-e347-5ac5-bd80-c71b391178ad', '2ccca276-af89-53ed-a994-e6184f34c8a1', 'f4bfa491-3bf2-5dd8-bffd-fb9a549f0619', 'd3659304-b61d-584f-9645-7d44924c67c2', 'ACCEPTED', null, true, now() - interval '25 days', now() - interval '2 days', now() - interval '24 days'),
    ('fc28e7c6-3a41-5243-9a80-54608a29ffc2', '7d2cdb05-61b8-5ba3-be52-8ced7f05ba38', '0690df46-4876-5e49-840d-60ea11454e04', 'd3659304-b61d-584f-9645-7d44924c67c2', 'ACCEPTED', null, true, now() - interval '9 days', now() + interval '5 days', now() - interval '8 days'),
    ('628f632b-1a8f-5d46-abaf-b88bc14f5257', '7d2cdb05-61b8-5ba3-be52-8ced7f05ba38', '2378aa43-06b4-524a-aa56-c020741fd473', 'd3659304-b61d-584f-9645-7d44924c67c2', 'ACCEPTED', null, true, now() - interval '9 days', now() + interval '5 days', now() - interval '8 days'),
    ('5395d816-b0e6-51db-acc8-8174122da2ac', '7d2cdb05-61b8-5ba3-be52-8ced7f05ba38', '8f9052d0-c609-5ec7-a763-886f7b027d75', 'd3659304-b61d-584f-9645-7d44924c67c2', 'ACCEPTED', null, true, now() - interval '9 days', now() + interval '5 days', now() - interval '8 days'),
    ('aa764dd2-6418-55ba-a6cc-1b56a877b35e', '80704f32-7c1c-5df3-891a-8bb6b0617c1f', '8f9052d0-c609-5ec7-a763-886f7b027d75', 'd3659304-b61d-584f-9645-7d44924c67c2', 'ACCEPTED', null, true, now() - interval '20 days', now() - interval '1 days', now() - interval '19 days'),
    ('dbd4d932-52bb-570f-aa0d-06b0fc3edc14', '80704f32-7c1c-5df3-891a-8bb6b0617c1f', '25065662-7411-554f-83a0-8ea03cd19228', 'd3659304-b61d-584f-9645-7d44924c67c2', 'ACCEPTED', null, true, now() - interval '20 days', now() - interval '1 days', now() - interval '19 days'),
    ('7ba3a0f0-40be-5f8b-9147-049c917ed871', '80704f32-7c1c-5df3-891a-8bb6b0617c1f', '0690df46-4876-5e49-840d-60ea11454e04', 'd3659304-b61d-584f-9645-7d44924c67c2', 'ACCEPTED', null, true, now() - interval '20 days', now() - interval '1 days', now() - interval '19 days'),
    ('f80c3966-7508-5200-9fad-bd237c49a3ff', '80704f32-7c1c-5df3-891a-8bb6b0617c1f', 'f6607090-6b6b-56c1-bc8c-1076fae6bf09', 'd3659304-b61d-584f-9645-7d44924c67c2', 'ACCEPTED', null, true, now() - interval '20 days', now() - interval '1 days', now() - interval '19 days'),
    ('3a39d5da-6eb9-5349-ae93-eb10e20b9657', '80704f32-7c1c-5df3-891a-8bb6b0617c1f', 'f4bfa491-3bf2-5dd8-bffd-fb9a549f0619', 'd3659304-b61d-584f-9645-7d44924c67c2', 'ACCEPTED', null, true, now() - interval '20 days', now() - interval '1 days', now() - interval '19 days'),
    ('95f64316-caef-572c-9afe-39c9184ebb86', '80704f32-7c1c-5df3-891a-8bb6b0617c1f', '98a9648c-30e0-50e1-b8b0-e8079d7f9a13', 'd3659304-b61d-584f-9645-7d44924c67c2', 'ACCEPTED', null, true, now() - interval '20 days', now() - interval '1 days', now() - interval '19 days'),
    ('cb2593ca-da9b-59f2-aa58-d0d834a00ac1', '80704f32-7c1c-5df3-891a-8bb6b0617c1f', 'e4c33d44-0eeb-547d-880f-9b6fd6eca987', 'd3659304-b61d-584f-9645-7d44924c67c2', 'ACCEPTED', null, true, now() - interval '20 days', now() - interval '1 days', now() - interval '19 days'),
    ('b78354fe-05be-5473-a8cb-579e7a4be703', '80704f32-7c1c-5df3-891a-8bb6b0617c1f', '2378aa43-06b4-524a-aa56-c020741fd473', 'd3659304-b61d-584f-9645-7d44924c67c2', 'ACCEPTED', null, true, now() - interval '20 days', now() - interval '1 days', now() - interval '19 days'),
    ('1f72e6fc-f86b-5c62-a0eb-388f044d9c06', '1d3809ad-4d20-5a70-9bba-5c1da8fde09c', '25065662-7411-554f-83a0-8ea03cd19228', 'd3659304-b61d-584f-9645-7d44924c67c2', 'ACCEPTED', null, true, now() - interval '30 days', now() - interval '6 days', now() - interval '29 days'),
    ('984d98ad-2eb1-5394-8fc1-bcf366f08979', '1d3809ad-4d20-5a70-9bba-5c1da8fde09c', '8f9052d0-c609-5ec7-a763-886f7b027d75', 'd3659304-b61d-584f-9645-7d44924c67c2', 'ACCEPTED', null, true, now() - interval '30 days', now() - interval '6 days', now() - interval '29 days'),
    ('c3c95e38-acdd-5bf6-9538-031b7445be12', '1d3809ad-4d20-5a70-9bba-5c1da8fde09c', 'f6607090-6b6b-56c1-bc8c-1076fae6bf09', 'd3659304-b61d-584f-9645-7d44924c67c2', 'ACCEPTED', null, true, now() - interval '30 days', now() - interval '6 days', now() - interval '29 days'),
    ('912a9aa0-b980-5546-8197-786f6b74b935', '97e0380d-fa0d-5744-8fc3-5c6d9947d7d3', '98a9648c-30e0-50e1-b8b0-e8079d7f9a13', 'd3659304-b61d-584f-9645-7d44924c67c2', 'ACCEPTED', null, true, now() - interval '30 days', now() - interval '6 days', now() - interval '29 days'),
    ('2670a58a-11de-50bf-9c9e-87cba29a478c', '97e0380d-fa0d-5744-8fc3-5c6d9947d7d3', '25065662-7411-554f-83a0-8ea03cd19228', 'd3659304-b61d-584f-9645-7d44924c67c2', 'ACCEPTED', null, true, now() - interval '30 days', now() - interval '6 days', now() - interval '29 days'),
    ('24894196-1f04-5815-abfc-ef6959b88f75', '97e0380d-fa0d-5744-8fc3-5c6d9947d7d3', 'e4c33d44-0eeb-547d-880f-9b6fd6eca987', 'd3659304-b61d-584f-9645-7d44924c67c2', 'ACCEPTED', null, true, now() - interval '30 days', now() - interval '6 days', now() - interval '29 days'),
    ('3ac35019-6e9e-5082-b127-c2d222bc6401', '2ccca276-af89-53ed-a994-e6184f34c8a1', 'f6607090-6b6b-56c1-bc8c-1076fae6bf09', 'd3659304-b61d-584f-9645-7d44924c67c2', 'DECLINED', 'No capacity in the required timeframe.', true, now() - interval '26 days', now() - interval '2 days', now() - interval '25 days');

-- bid: 21 rows.
insert into public.bid (
    id,
    job_id,
    invitation_id,
    contractor_org_id,
    amount,
    day_rate,
    estimated_days,
    proposed_start,
    approach,
    state,
    version,
    reject_reason_code,
    staff_note,
    contact_flagged,
    is_demo,
    created_at,
    submitted_at,
    released_at
) values
    ('1d685adb-0ea0-59f0-bcb4-cf713cb90258', '7d2cdb05-61b8-5ba3-be52-8ced7f05ba38', '15dba115-45ae-5531-a74b-248c713a3616', 'b96d47a6-0baa-5ce8-bfe6-b1c37b923d31', 31500.0, null, 18, (now() + interval '35 days')::date, 'Site survey at each clinic first, then a staged cutover outside clinic hours with the existing access points left in place until each site is signed off.', 'DRAFT', 1, null, null, false, true, now() - interval '2 days', null, null),
    ('77aac3e5-114a-5795-9b16-53d36967fc06', '2ccca276-af89-53ed-a994-e6184f34c8a1', '70a95579-80a4-5d0d-9f77-8d7e6c287370', '8f9052d0-c609-5ec7-a763-886f7b027d75', 24500.0, null, 21, (now() + interval '35 days')::date, 'Staged mailbox migration with a hybrid configuration, cutover over two weekends.', 'RELEASED', 1, null, null, false, true, now() - interval '9 days', now() - interval '8 days', now() - interval '1 days'),
    ('38ffd7b5-bcc6-53e1-9f12-c5461eacca25', '2ccca276-af89-53ed-a994-e6184f34c8a1', '73ad08ff-28de-5822-8f6b-8a78881cb9d4', '25065662-7411-554f-83a0-8ea03cd19228', 28900.0, null, 26, (now() + interval '35 days')::date, 'Full discovery first, then a big-bang cutover on a single weekend with a rollback plan.', 'RELEASED', 1, null, null, false, true, now() - interval '9 days', now() - interval '8 days', now() - interval '1 days'),
    ('b94ad633-851c-5432-ab78-813945b6d054', '2ccca276-af89-53ed-a994-e6184f34c8a1', 'dc05f9c7-e347-5ac5-bd80-c71b391178ad', 'f4bfa491-3bf2-5dd8-bffd-fb9a549f0619', 21750.0, null, 19, (now() + interval '35 days')::date, 'Cloud-only migration with a third party migration tool, MFA and conditional access enforced at completion.', 'RELEASED', 1, null, null, false, true, now() - interval '9 days', now() - interval '8 days', now() - interval '1 days'),
    ('1457a20b-e98f-58e4-8155-e5ac0bb1cc44', '7d2cdb05-61b8-5ba3-be52-8ced7f05ba38', 'fc28e7c6-3a41-5243-9a80-54608a29ffc2', '0690df46-4876-5e49-840d-60ea11454e04', 33400.0, null, 15, (now() + interval '35 days')::date, 'Predictive survey, then on-site validation. Hardware supplied at cost plus ten per cent.', 'SUBMITTED', 1, null, null, false, true, now() - interval '4 days', now() - interval '3 days', null),
    ('a9eab02a-cfce-515f-89ea-3c66945a8edc', '7d2cdb05-61b8-5ba3-be52-8ced7f05ba38', '628f632b-1a8f-5d46-abaf-b88bc14f5257', '2378aa43-06b4-524a-aa56-c020741fd473', 29950.0, null, 20, (now() + interval '35 days')::date, 'Full replacement with structured cabling remediation where the existing runs will not support the new units.', 'SUBMITTED', 1, null, null, false, true, now() - interval '4 days', now() - interval '3 days', null),
    ('7700e4fc-bc3c-54cd-bfa9-2f8c31546257', '7d2cdb05-61b8-5ba3-be52-8ced7f05ba38', '5395d816-b0e6-51db-acc8-8174122da2ac', '8f9052d0-c609-5ec7-a763-886f7b027d75', 36100.0, null, 14, (now() + interval '35 days')::date, 'Same-vendor replacement to keep the existing management plane, with guest network segmentation.', 'SUBMITTED', 1, null, null, false, true, now() - interval '4 days', now() - interval '3 days', null),
    ('9b9f87bc-bb2a-59cf-8717-52f363a6774b', '80704f32-7c1c-5df3-891a-8bb6b0617c1f', 'aa764dd2-6418-55ba-a6cc-1b56a877b35e', '8f9052d0-c609-5ec7-a763-886f7b027d75', null, 1250.0, 120, (now() + interval '35 days')::date, 'Transition over eight weeks from our NSW operations centre, with a shadowing period and an agreed knowledge base handover.', 'SUBMITTED', 1, null, null, false, true, now() - interval '3 days', now() - interval '2 days', null),
    ('778f0a29-11c7-5dfc-8158-6b0f375bbc87', '80704f32-7c1c-5df3-891a-8bb6b0617c1f', 'dbd4d932-52bb-570f-aa0d-06b0fc3edc14', '25065662-7411-554f-83a0-8ea03cd19228', null, 1390.0, 120, (now() + interval '35 days')::date, 'Transition over eight weeks from our SA operations centre, with a shadowing period and an agreed knowledge base handover.', 'SUBMITTED', 1, null, null, false, true, now() - interval '3 days', now() - interval '2 days', null),
    ('565344e0-e087-58d8-9981-88d8ca10a6f0', '80704f32-7c1c-5df3-891a-8bb6b0617c1f', '7ba3a0f0-40be-5f8b-9147-049c917ed871', '0690df46-4876-5e49-840d-60ea11454e04', null, 1450.0, 120, (now() + interval '35 days')::date, 'Transition over eight weeks from our QLD operations centre, with a shadowing period and an agreed knowledge base handover.', 'SUBMITTED', 1, null, null, false, true, now() - interval '3 days', now() - interval '2 days', null),
    ('9f8bf4a9-16cc-5a0c-a1a7-8f99e7f6ecfb', '80704f32-7c1c-5df3-891a-8bb6b0617c1f', 'f80c3966-7508-5200-9fad-bd237c49a3ff', 'f6607090-6b6b-56c1-bc8c-1076fae6bf09', null, 1180.0, 120, (now() + interval '35 days')::date, 'Transition over eight weeks from our WA operations centre, with a shadowing period and an agreed knowledge base handover.', 'SUBMITTED', 1, null, null, false, true, now() - interval '3 days', now() - interval '2 days', null),
    ('7b0c963f-0cca-5849-aa44-ff02e3f1bd78', '80704f32-7c1c-5df3-891a-8bb6b0617c1f', '3a39d5da-6eb9-5349-ae93-eb10e20b9657', 'f4bfa491-3bf2-5dd8-bffd-fb9a549f0619', null, 1520.0, 120, (now() + interval '35 days')::date, 'Transition over eight weeks from our VIC operations centre, with a shadowing period and an agreed knowledge base handover.', 'SUBMITTED', 1, null, null, false, true, now() - interval '3 days', now() - interval '2 days', null),
    ('e8bba257-f425-5aa9-8b6f-2d8434c685fe', '80704f32-7c1c-5df3-891a-8bb6b0617c1f', '95f64316-caef-572c-9afe-39c9184ebb86', '98a9648c-30e0-50e1-b8b0-e8079d7f9a13', null, 1320.0, 120, (now() + interval '35 days')::date, 'Transition over eight weeks from our SA operations centre, with a shadowing period and an agreed knowledge base handover.', 'SUBMITTED', 1, null, null, false, true, now() - interval '3 days', now() - interval '2 days', null),
    ('9d9f7896-dda6-5215-adf0-39bd8fb8c6c5', '80704f32-7c1c-5df3-891a-8bb6b0617c1f', 'cb2593ca-da9b-59f2-aa58-d0d834a00ac1', 'e4c33d44-0eeb-547d-880f-9b6fd6eca987', null, 1610.0, 120, (now() + interval '35 days')::date, 'Transition over eight weeks from our TAS operations centre, with a shadowing period and an agreed knowledge base handover.', 'SUBMITTED', 1, null, null, false, true, now() - interval '3 days', now() - interval '2 days', null),
    ('615f4e3e-3c5e-5593-81d2-3df9eae01884', '80704f32-7c1c-5df3-891a-8bb6b0617c1f', 'b78354fe-05be-5473-a8cb-579e7a4be703', '2378aa43-06b4-524a-aa56-c020741fd473', null, 1275.0, 120, (now() + interval '35 days')::date, 'Transition over eight weeks from our NT operations centre, with a shadowing period and an agreed knowledge base handover.', 'SUBMITTED', 1, null, null, false, true, now() - interval '3 days', now() - interval '2 days', null),
    ('c71a8816-4851-5168-a49e-0cc3c5335810', '1d3809ad-4d20-5a70-9bba-5c1da8fde09c', '1f72e6fc-f86b-5c62-a0eb-388f044d9c06', '25065662-7411-554f-83a0-8ea03cd19228', 92000.0, null, 60, (now() + interval '35 days')::date, 'Detailed delivery plan supplied with the bid, including a named delivery lead and a fortnightly steering report.', 'RELEASED', 1, null, null, false, true, now() - interval '13 days', now() - interval '12 days', now() - interval '1 days'),
    ('ecbb225f-be51-58dc-87cd-e40768269493', '1d3809ad-4d20-5a70-9bba-5c1da8fde09c', '984d98ad-2eb1-5394-8fc1-bcf366f08979', '8f9052d0-c609-5ec7-a763-886f7b027d75', 104500.0, null, 70, (now() + interval '35 days')::date, 'Alternative approach with a longer discovery phase.', 'RELEASED', 1, null, null, false, true, now() - interval '13 days', now() - interval '12 days', now() - interval '1 days'),
    ('ae070bd4-e877-562c-b7bc-3467632ab79c', '1d3809ad-4d20-5a70-9bba-5c1da8fde09c', 'c3c95e38-acdd-5bf6-9538-031b7445be12', 'f6607090-6b6b-56c1-bc8c-1076fae6bf09', 88400.0, null, 55, (now() + interval '35 days')::date, 'Alternative approach with a longer discovery phase.', 'RELEASED', 1, null, null, false, true, now() - interval '13 days', now() - interval '12 days', now() - interval '1 days'),
    ('64f5ea7f-d5d4-50c4-99fe-f3b0630c59d0', '97e0380d-fa0d-5744-8fc3-5c6d9947d7d3', '912a9aa0-b980-5546-8197-786f6b74b935', '98a9648c-30e0-50e1-b8b0-e8079d7f9a13', 51800.0, null, 45, (now() + interval '35 days')::date, 'Detailed delivery plan supplied with the bid, including a named delivery lead and a fortnightly steering report.', 'RELEASED', 1, null, null, false, true, now() - interval '13 days', now() - interval '12 days', now() - interval '1 days'),
    ('9e407ca9-6ff5-5342-a585-86dbba7f3eb4', '97e0380d-fa0d-5744-8fc3-5c6d9947d7d3', '2670a58a-11de-50bf-9c9e-87cba29a478c', '25065662-7411-554f-83a0-8ea03cd19228', 62300.0, null, 52, (now() + interval '35 days')::date, 'Alternative approach with a longer discovery phase.', 'RELEASED', 1, null, null, false, true, now() - interval '13 days', now() - interval '12 days', now() - interval '1 days'),
    ('2bf46963-842f-5a68-a809-8d2a78283d24', '97e0380d-fa0d-5744-8fc3-5c6d9947d7d3', '24894196-1f04-5815-abfc-ef6959b88f75', 'e4c33d44-0eeb-547d-880f-9b6fd6eca987', 57000.0, null, 48, (now() + interval '35 days')::date, 'Alternative approach with a longer discovery phase.', 'RELEASED', 1, null, null, false, true, now() - interval '13 days', now() - interval '12 days', now() - interval '1 days');

-- bid_version: 21 rows.
insert into public.bid_version (
    id,
    bid_id,
    version,
    snapshot,
    is_demo,
    created_at
) values
    ('725c6103-7a89-50ad-9181-2ce2c8a4636a', '1d685adb-0ea0-59f0-bcb4-cf713cb90258', 1, '{"amount":31500,"approach":"Site survey at each clinic first, then a staged cutover outside clinic hours with the existing access points left in place until each site is signed off.","estimated_days":18}', true, now() - interval '1 days'),
    ('45c88114-b1ad-539f-9c03-e3fc87cd18bc', '77aac3e5-114a-5795-9b16-53d36967fc06', 1, '{"amount":24500,"approach":"Staged mailbox migration with a hybrid configuration, cutover over two weekends.","estimated_days":21}', true, now() - interval '8 days'),
    ('68eb14f5-12fb-5d50-a01e-1b6ede515ea1', '38ffd7b5-bcc6-53e1-9f12-c5461eacca25', 1, '{"amount":28900,"approach":"Full discovery first, then a big-bang cutover on a single weekend with a rollback plan.","estimated_days":26}', true, now() - interval '8 days'),
    ('c5fb538e-f62d-5ee0-b5cd-0f3148bd0bc2', 'b94ad633-851c-5432-ab78-813945b6d054', 1, '{"amount":21750,"approach":"Cloud-only migration with a third party migration tool, MFA and conditional access enforced at completion.","estimated_days":19}', true, now() - interval '8 days'),
    ('1bf73ed2-6500-58c8-af89-b1e7fa824e94', '1457a20b-e98f-58e4-8155-e5ac0bb1cc44', 1, '{"amount":33400,"approach":"Predictive survey, then on-site validation. Hardware supplied at cost plus ten per cent.","estimated_days":15}', true, now() - interval '3 days'),
    ('3a530bcc-e8ad-5196-9f07-d74a38a2a19d', 'a9eab02a-cfce-515f-89ea-3c66945a8edc', 1, '{"amount":29950,"approach":"Full replacement with structured cabling remediation where the existing runs will not support the new units.","estimated_days":20}', true, now() - interval '3 days'),
    ('77f9981a-b198-5f00-b2f1-05bd844d19b1', '7700e4fc-bc3c-54cd-bfa9-2f8c31546257', 1, '{"amount":36100,"approach":"Same-vendor replacement to keep the existing management plane, with guest network segmentation.","estimated_days":14}', true, now() - interval '3 days'),
    ('f30fa41a-0e47-521f-9106-1ac574357e35', '9b9f87bc-bb2a-59cf-8717-52f363a6774b', 1, '{"amount":null,"approach":"Transition over eight weeks from our NSW operations centre, with a shadowing period and an agreed knowledge base handover.","estimated_days":120}', true, now() - interval '2 days'),
    ('c8dd218d-8ae3-57a5-8a1a-14875c14e79b', '778f0a29-11c7-5dfc-8158-6b0f375bbc87', 1, '{"amount":null,"approach":"Transition over eight weeks from our SA operations centre, with a shadowing period and an agreed knowledge base handover.","estimated_days":120}', true, now() - interval '2 days'),
    ('dfae5dd5-02c1-5c3e-87bb-45aacd589642', '565344e0-e087-58d8-9981-88d8ca10a6f0', 1, '{"amount":null,"approach":"Transition over eight weeks from our QLD operations centre, with a shadowing period and an agreed knowledge base handover.","estimated_days":120}', true, now() - interval '2 days'),
    ('4fd26eee-ea52-5194-966c-c7e905e5d6c0', '9f8bf4a9-16cc-5a0c-a1a7-8f99e7f6ecfb', 1, '{"amount":null,"approach":"Transition over eight weeks from our WA operations centre, with a shadowing period and an agreed knowledge base handover.","estimated_days":120}', true, now() - interval '2 days'),
    ('63ea26a7-c7df-526c-9581-1abce53044ac', '7b0c963f-0cca-5849-aa44-ff02e3f1bd78', 1, '{"amount":null,"approach":"Transition over eight weeks from our VIC operations centre, with a shadowing period and an agreed knowledge base handover.","estimated_days":120}', true, now() - interval '2 days'),
    ('e8357280-aae0-53ba-a4f4-ca92fee9cf9c', 'e8bba257-f425-5aa9-8b6f-2d8434c685fe', 1, '{"amount":null,"approach":"Transition over eight weeks from our SA operations centre, with a shadowing period and an agreed knowledge base handover.","estimated_days":120}', true, now() - interval '2 days'),
    ('3435dd46-eff4-560f-bdd5-1c9be9a88fd2', '9d9f7896-dda6-5215-adf0-39bd8fb8c6c5', 1, '{"amount":null,"approach":"Transition over eight weeks from our TAS operations centre, with a shadowing period and an agreed knowledge base handover.","estimated_days":120}', true, now() - interval '2 days'),
    ('ec21bfa1-cfd0-5a64-bc53-a14a991342b3', '615f4e3e-3c5e-5593-81d2-3df9eae01884', 1, '{"amount":null,"approach":"Transition over eight weeks from our NT operations centre, with a shadowing period and an agreed knowledge base handover.","estimated_days":120}', true, now() - interval '2 days'),
    ('dcce97b9-1263-5666-8a91-ae1a5f96a837', 'c71a8816-4851-5168-a49e-0cc3c5335810', 1, '{"amount":92000,"approach":"Detailed delivery plan supplied with the bid, including a named delivery lead and a fortnightly steering report.","estimated_days":60}', true, now() - interval '12 days'),
    ('31f898bc-d6a0-515d-90fc-67f1c497fcff', 'ecbb225f-be51-58dc-87cd-e40768269493', 1, '{"amount":104500,"approach":"Alternative approach with a longer discovery phase.","estimated_days":70}', true, now() - interval '12 days'),
    ('597773af-5a63-5c07-93a8-e46511e35b3d', 'ae070bd4-e877-562c-b7bc-3467632ab79c', 1, '{"amount":88400,"approach":"Alternative approach with a longer discovery phase.","estimated_days":55}', true, now() - interval '12 days'),
    ('d2f7e656-4821-519c-afc3-38bd6338393b', '64f5ea7f-d5d4-50c4-99fe-f3b0630c59d0', 1, '{"amount":51800,"approach":"Detailed delivery plan supplied with the bid, including a named delivery lead and a fortnightly steering report.","estimated_days":45}', true, now() - interval '12 days'),
    ('2515c9dc-fd1f-5d80-b7fb-c75fecd48b57', '9e407ca9-6ff5-5342-a585-86dbba7f3eb4', 1, '{"amount":62300,"approach":"Alternative approach with a longer discovery phase.","estimated_days":52}', true, now() - interval '12 days'),
    ('37744a9e-d767-5849-a6a7-fabcd92e1dbf', '2bf46963-842f-5a68-a809-8d2a78283d24', 1, '{"amount":57000,"approach":"Alternative approach with a longer discovery phase.","estimated_days":48}', true, now() - interval '12 days');

-- award: 2 rows.
insert into public.award (
    id,
    job_id,
    bid_id,
    selected_by_client_user_id,
    confirmed_by_staff_id,
    state,
    is_demo,
    selected_at,
    awarded_at
) values
    ('57796bee-4bbe-57be-9167-e005c00c6aac', '1d3809ad-4d20-5a70-9bba-5c1da8fde09c', 'c71a8816-4851-5168-a49e-0cc3c5335810', '6c9934d6-6586-51c3-b595-83cb7d2606a8', null, 'PENDING', true, now() - interval '1 days', null),
    ('570a9951-a197-5301-b3b1-960719adf423', '97e0380d-fa0d-5744-8fc3-5c6d9947d7d3', '64f5ea7f-d5d4-50c4-99fe-f3b0630c59d0', '6c9934d6-6586-51c3-b595-83cb7d2606a8', null, 'PENDING', true, now() - interval '1 days', null);

-- audit_event: 6 rows.
insert into public.audit_event (
    id,
    actor_user_id,
    actor_role,
    entity_type,
    entity_id,
    action,
    before_state,
    after_state,
    reason_code,
    note,
    ip,
    is_demo,
    occurred_at
) values
    ('cb918238-acf8-5f27-a059-f606ee581e0a', 'd3659304-b61d-584f-9645-7d44924c67c2', 'STAFF', 'organisation', '8efa56b1-0421-5bd7-8b5d-f8775c979155', 'verification.approved', '{"status":"PENDING"}', '{"status":"VERIFIED"}', 'OTHER', 'ABR entity name matched exactly.', '203.0.113.24', true, now() - interval '60 days'),
    ('af7c0d85-0387-5e1b-a817-d5f3f9ac2a43', '223396be-ddff-5ea2-8ef8-9c9015f07410', 'CLIENT', 'job', '2ccca276-af89-53ed-a994-e6184f34c8a1', 'job.submitted', '{"state":"DRAFT"}', '{"state":"PENDING_APPROVAL"}', null, null, '203.0.113.24', true, now() - interval '33 days'),
    ('d6fb2c11-e917-50ef-8b01-0d0ef0670e6f', 'd3659304-b61d-584f-9645-7d44924c67c2', 'STAFF', 'job', '2ccca276-af89-53ed-a994-e6184f34c8a1', 'job.approved', '{"state":"PENDING_APPROVAL"}', '{"state":"APPROVED"}', null, 'Budget realistic, scope clear.', '203.0.113.24', true, now() - interval '32 days'),
    ('7c1999ed-ac50-5d86-826b-24486f7bdd4f', 'd3659304-b61d-584f-9645-7d44924c67c2', 'STAFF', 'job', '2ccca276-af89-53ed-a994-e6184f34c8a1', 'invitations.issued', '{"state":"APPROVED"}', '{"state":"INVITING"}', null, 'Four contractors invited on skill and region match.', '203.0.113.24', true, now() - interval '25 days'),
    ('3706bbc8-ae37-59aa-8f3b-2181de953c50', null, 'system', 'job', '2ccca276-af89-53ed-a994-e6184f34c8a1', 'bidding.closed', '{"state":"BIDDING"}', '{"state":"BIDS_CLOSED"}', null, 'Closing date reached.', '203.0.113.24', true, now() - interval '2 days'),
    ('880fd59f-2342-512e-b0fa-aaa5344b4cd7', 'd3659304-b61d-584f-9645-7d44924c67c2', 'STAFF', 'job', '2ccca276-af89-53ed-a994-e6184f34c8a1', 'bids.released', '{"state":"BIDS_CLOSED"}', '{"state":"BIDS_RELEASED"}', null, 'Three bids released, none rejected.', '203.0.113.24', true, now() - interval '1 days');

-- notification: 3 rows.
insert into public.notification (
    id,
    user_id,
    to_address,
    channel,
    template,
    subject,
    body,
    state,
    attempts,
    last_error,
    is_demo,
    created_at,
    sent_at
) values
    ('4c5faeb9-a79b-5493-9e03-1b3245dcd596', '223396be-ddff-5ea2-8ef8-9c9015f07410', 'client@demo.ab-verified.invalid', 'EMAIL', 'bid_released', 'Bids are ready to review', 'Bids on ''Migrate 40 staff from on-premise Exchange to Microsoft 365'' have been released for your review.', 'SUPPRESSED', 0, null, true, now() - interval '1 days', now() - interval '1 days'),
    ('c896faf0-5e66-56f7-9d67-2658f924b962', '19eb3f61-4c34-53d2-9b0d-0644998340e5', 'contractor@demo.ab-verified.invalid', 'EMAIL', 'invitation_sent', 'You have been invited to bid', 'You have been invited to bid on ''Legacy AS/400 report migration''. Invitations close in nine days.', 'SUPPRESSED', 0, null, true, now() - interval '2 days', now() - interval '2 days'),
    ('8e3463a0-72fc-51e7-9e06-46cc09677bc9', '19eb3f61-4c34-53d2-9b0d-0644998340e5', '+61400000000', 'SMS', 'invitation_sent', null, 'AB-Verified: you have a new invitation to bid. Sign in to respond.', 'SUPPRESSED', 0, null, true, now() - interval '2 days', now() - interval '2 days');

-- queue_message: no rows in the fixture.

-- ---------------------------------------------------------------------------
-- Back to forced and audited before the transaction commits.
-- ---------------------------------------------------------------------------
alter table public.audit_event enable row level security;
alter table public.notification enable row level security;
alter table public.queue_message enable row level security;
alter table public.bid_version enable row level security;
alter table public.award enable row level security;
alter table public.bid enable row level security;
alter table public.invitation enable row level security;
alter table public.job enable row level security;
alter table public.verification_decision enable row level security;
alter table public.verification_case enable row level security;
alter table public.document enable row level security;
alter table public.contact_token enable row level security;
alter table public.abn_record enable row level security;
alter table public.user_profile enable row level security;
alter table public.organisation enable row level security;

alter table public.organisation enable trigger user;
alter table public.verification_case enable trigger user;
alter table public.verification_decision enable trigger user;
alter table public.job enable trigger user;
alter table public.invitation enable trigger user;
alter table public.bid enable trigger user;
alter table public.award enable trigger user;
alter table public.document enable trigger user;

commit;
