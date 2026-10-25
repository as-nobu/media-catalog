"""Canonical search-cache invalidation triggers, shared with backup validation."""
import re

TRIGGERS = {
    'visual_search_insert': '''CREATE TRIGGER IF NOT EXISTS visual_search_insert AFTER INSERT ON visual_features BEGIN
                    INSERT INTO visual_search_state VALUES (NEW.engine,1)
                    ON CONFLICT(engine) DO UPDATE SET generation=generation+1;
                END;''',
    'visual_search_delete': '''CREATE TRIGGER IF NOT EXISTS visual_search_delete AFTER DELETE ON visual_features BEGIN
                    INSERT INTO visual_search_state VALUES (OLD.engine,1)
                    ON CONFLICT(engine) DO UPDATE SET generation=generation+1;
                END;''',
    'visual_search_update': '''CREATE TRIGGER IF NOT EXISTS visual_search_update AFTER UPDATE ON visual_features BEGIN
                    INSERT INTO visual_search_state VALUES (OLD.engine,1)
                    ON CONFLICT(engine) DO UPDATE SET generation=generation+1;
                    INSERT INTO visual_search_state SELECT NEW.engine,1 WHERE NEW.engine!=OLD.engine
                    ON CONFLICT(engine) DO UPDATE SET generation=generation+1;
                END;''',
    'visual_search_file_update': '''CREATE TRIGGER IF NOT EXISTS visual_search_file_update
                AFTER UPDATE OF missing,registration_uid,revision ON files
                WHEN OLD.missing!=NEW.missing OR OLD.registration_uid!=NEW.registration_uid OR OLD.revision!=NEW.revision
                BEGIN UPDATE visual_search_state SET generation=generation+1; END;''',
    'visual_search_file_delete': '''CREATE TRIGGER IF NOT EXISTS visual_search_file_delete AFTER DELETE ON files
                BEGIN UPDATE visual_search_state SET generation=generation+1; END;''',
}


def known_trigger(name, sql):
    expected = TRIGGERS.get(name)
    if expected is None or sql is None: return False
    # sqlite_master omits IF NOT EXISTS. Otherwise accept only our exact SQL,
    # allowing whitespace/case changes, never merely a matching trigger name.
    def canonical(value):
        return re.sub(r'\s+', ' ', value).strip().rstrip(';').casefold().replace(' if not exists ', ' ')
    return canonical(sql) == canonical(expected)
