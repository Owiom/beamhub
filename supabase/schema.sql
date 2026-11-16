-- Схема BeamHub для Supabase (PostgreSQL).
-- Выполнить в Supabase → SQL Editor → New query → Run.
-- Модераторы и админы хранятся в public.user_roles и проверяются RLS-политиками.

create table if not exists public.profiles (
    id uuid primary key references auth.users (id) on delete cascade,
    nickname text not null,
    bio text default '',
    avatar_url text default '',
    avatar_color text default '#ff7a1a',
    created_at timestamptz default now(),
    last_seen timestamptz default now()
);

create table if not exists public.user_roles (
    user_id uuid primary key references auth.users (id) on delete cascade,
    role text not null default 'user' check (role in ('user', 'moderator', 'admin'))
);

create or replace function public.current_role()
returns text language sql stable security definer as $$
    select coalesce((select role from public.user_roles where user_id = auth.uid()), 'user');
$$;

create or replace function public.is_moderator()
returns boolean language sql stable security definer as $$
    select public.current_role() in ('moderator', 'admin');
$$;

create table if not exists public.collections (
    -- id приходит из клиента (col_xxx): так локальные и облачные сборки совпадают
    id text primary key,
    owner_id uuid not null references auth.users (id) on delete cascade,
    title text not null check (char_length(title) between 3 and 80),
    description text default '',
    cover text default '',
    category text default 'other',
    tags text[] default '{}',
    published boolean default false,
    game_version text default '',
    version int default 1,
    views int default 0,
    installs int default 0,
    downloads int default 0,
    created_at timestamptz default now(),
    updated_at timestamptz default now()
);

create table if not exists public.collection_items (
    collection_id text not null references public.collections (id) on delete cascade,
    mod_id text not null,
    position int default 0,
    note text default '',
    added_at timestamptz default now(),
    primary key (collection_id, mod_id)
);

create table if not exists public.collection_versions (
    id bigserial primary key,
    collection_id text not null references public.collections (id) on delete cascade,
    version int not null,
    snapshot jsonb default '[]'::jsonb,
    note text default '',
    created_at timestamptz default now()
);

create table if not exists public.comments (
    id uuid primary key default gen_random_uuid(),
    target_type text not null check (target_type in ('collection', 'mod')),
    target_id text not null,
    user_id uuid not null references auth.users (id) on delete cascade,
    parent_id uuid references public.comments (id) on delete set null,
    body text not null check (char_length(body) between 1 and 1500),
    deleted boolean default false,
    created_at timestamptz default now(),
    updated_at timestamptz default now()
);

create table if not exists public.ratings (
    target_type text not null check (target_type in ('collection', 'mod')),
    target_id text not null,
    user_id uuid not null references auth.users (id) on delete cascade,
    stars int not null check (stars between 1 and 5),
    review text default '' check (char_length(review) <= 2000),
    installed boolean default false,
    created_at timestamptz default now(),
    updated_at timestamptz default now(),
    primary key (target_type, target_id, user_id)
);

create table if not exists public.reports (
    id uuid primary key default gen_random_uuid(),
    target_type text not null,
    target_id text not null,
    user_id uuid not null references auth.users (id) on delete cascade,
    reason text default 'other',
    details text default '',
    status text default 'open' check (status in ('open', 'resolved', 'rejected')),
    resolved_by uuid references auth.users (id),
    resolved_at timestamptz,
    created_at timestamptz default now()
);

create table if not exists public.admin_actions (
    id bigserial primary key,
    actor_id uuid references auth.users (id) on delete set null,
    action text not null,
    target_type text default '',
    target_id text default '',
    note text default '',
    created_at timestamptz default now()
);

create table if not exists public.mod_stats (
    mod_id text primary key,
    views int default 0,
    downloads int default 0,
    installs int default 0
);

create index if not exists collections_pub_idx
    on public.collections (published, created_at desc);
create index if not exists comments_target_idx
    on public.comments (target_type, target_id, created_at);
create index if not exists ratings_target_idx
    on public.ratings (target_type, target_id);
create index if not exists reports_status_idx
    on public.reports (status, created_at);

-- ---------------------------------------------------------------- RLS
alter table public.profiles enable row level security;
alter table public.user_roles enable row level security;
alter table public.collections enable row level security;
alter table public.collection_items enable row level security;
alter table public.collection_versions enable row level security;
alter table public.comments enable row level security;
alter table public.ratings enable row level security;
alter table public.reports enable row level security;
alter table public.admin_actions enable row level security;
alter table public.mod_stats enable row level security;

-- профили: читают все, меняет только владелец
create policy "profiles read" on public.profiles for select using (true);
create policy "profiles upsert own" on public.profiles for insert
    with check (auth.uid() = id);
create policy "profiles update own" on public.profiles for update
    using (auth.uid() = id) with check (auth.uid() = id);

-- роли: читают все (нужно для бейджей), пишет только админ
create policy "roles read" on public.user_roles for select using (true);
create policy "roles admin write" on public.user_roles for all
    using (public.current_role() = 'admin') with check (public.current_role() = 'admin');

-- сборки: опубликованные видны всем, черновики — автору и модераторам
create policy "collections read" on public.collections for select
    using (published = true or owner_id = auth.uid() or public.is_moderator());
create policy "collections insert own" on public.collections for insert
    with check (owner_id = auth.uid());
create policy "collections update own" on public.collections for update
    using (owner_id = auth.uid() or public.is_moderator())
    with check (owner_id = auth.uid() or public.is_moderator());
create policy "collections delete own" on public.collections for delete
    using (owner_id = auth.uid() or public.is_moderator());

create policy "items read" on public.collection_items for select
    using (exists (select 1 from public.collections c where c.id = collection_id
        and (c.published = true or c.owner_id = auth.uid() or public.is_moderator())));
create policy "items write own" on public.collection_items for all
    using (exists (select 1 from public.collections c where c.id = collection_id
        and c.owner_id = auth.uid()))
    with check (exists (select 1 from public.collections c where c.id = collection_id
        and c.owner_id = auth.uid()));

create policy "versions read" on public.collection_versions for select using (true);
create policy "versions write own" on public.collection_versions for insert
    with check (exists (select 1 from public.collections c where c.id = collection_id
        and c.owner_id = auth.uid()));

-- комментарии: читают все, создаёт вошедший, меняет/удаляет автор или модератор
create policy "comments read" on public.comments for select using (true);
create policy "comments insert" on public.comments for insert
    with check (user_id = auth.uid());
create policy "comments update own" on public.comments for update
    using (user_id = auth.uid() or public.is_moderator())
    with check (user_id = auth.uid() or public.is_moderator());
create policy "comments delete own" on public.comments for delete
    using (user_id = auth.uid() or public.is_moderator());

-- оценки: читают все, ставит вошедший, одна на объект (первичный ключ)
create policy "ratings read" on public.ratings for select using (true);
create policy "ratings write own" on public.ratings for insert
    with check (user_id = auth.uid());
create policy "ratings update own" on public.ratings for update
    using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy "ratings delete own" on public.ratings for delete using (user_id = auth.uid());

-- жалобы: создаёт вошедший, читают модераторы
create policy "reports insert own" on public.reports for insert with check (user_id = auth.uid());
create policy "reports read staff" on public.reports for select
    using (user_id = auth.uid() or public.is_moderator());
create policy "reports update staff" on public.reports for update
    using (public.is_moderator()) with check (public.is_moderator());

create policy "actions read staff" on public.admin_actions for select
    using (public.is_moderator());
create policy "actions insert staff" on public.admin_actions for insert
    with check (public.is_moderator());

create policy "stats read" on public.mod_stats for select using (true);
create policy "stats write own" on public.mod_stats for all
    using (public.is_moderator()) with check (public.is_moderator());

-- счётчики просмотров/установок не даём крутить вручную: обновляются через RPC
create or replace function public.bump_collection(p_collection text, p_kind text)
returns void language plpgsql security definer as $$
begin
    if p_kind = 'view' then
        update public.collections set views = views + 1 where id = p_collection;
    elsif p_kind = 'download' then
        update public.collections set downloads = downloads + 1 where id = p_collection;
    elsif p_kind = 'install' then
        update public.collections set installs = installs + 1 where id = p_collection;
    end if;
end;
$$;

-- новому пользователю создаём профиль и роль
create or replace function public.handle_new_user()
returns trigger language plpgsql security definer as $$
begin
    insert into public.profiles (id, nickname)
    values (new.id, coalesce(
        new.raw_user_meta_data ->> 'full_name',
        new.raw_user_meta_data ->> 'name',
        split_part(coalesce(new.email, 'user'), '@', 1))::text)
    on conflict (id) do nothing;
    insert into public.user_roles (user_id, role) values (new.id, 'user')
    on conflict (user_id) do nothing;
    return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
    after insert on auth.users
    for each row execute function public.handle_new_user();
