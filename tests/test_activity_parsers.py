import pytest

from aiosyslogd.activity.parsers import (
    BaseActivityParser,
    FortiOSParser,
    ParsedActivity,
    get_activity_parser,
)


class TestParsedActivity:
    def test_all_fields(self):
        p = ParsedActivity(app="YouTube", user="alice", appcat="Video")
        assert p.app == "YouTube"
        assert p.user == "alice"
        assert p.appcat == "Video"

    def test_default_appcat(self):
        p = ParsedActivity(app="App", user="bob")
        assert p.appcat == "Unknown"


class TestFortiOSParser:
    def test_full_message(self):
        parser = FortiOSParser()
        msg = 'type="traffic" app="YouTube" user="alice" appcat="Video" srcip=10.0.0.1'
        result = parser.extract(msg)
        assert result is not None
        assert result.app == "YouTube"
        assert result.user == "alice"
        assert result.appcat == "Video"

    def test_missing_appcat_defaults_to_unknown(self):
        parser = FortiOSParser()
        msg = 'app="Gmail" user="bob" srcip=10.0.0.1'
        result = parser.extract(msg)
        assert result is not None
        assert result.app == "Gmail"
        assert result.user == "bob"
        assert result.appcat == "Unknown"

    def test_missing_user_returns_none(self):
        parser = FortiOSParser()
        msg = 'app="YouTube" appcat="Video"'
        result = parser.extract(msg)
        assert result is None

    def test_missing_app_returns_none(self):
        parser = FortiOSParser()
        msg = 'user="alice" appcat="Video"'
        result = parser.extract(msg)
        assert result is None

    def test_empty_message(self):
        parser = FortiOSParser()
        result = parser.extract("")
        assert result is None

    def test_irrelevant_message(self):
        parser = FortiOSParser()
        result = parser.extract("This log has no kv pairs at all")
        assert result is None

    def test_quoted_values_with_spaces(self):
        parser = FortiOSParser()
        msg = 'app="Google Drive" user="john.doe" appcat="Cloud Storage"'
        result = parser.extract(msg)
        assert result is not None
        assert result.app == "Google Drive"
        assert result.user == "john.doe"
        assert result.appcat == "Cloud Storage"

    def test_appcat_match_in_middle_of_field_name(self):
        parser = FortiOSParser()
        msg = 'app="X" user="Y" notappcat="Z" appcat="Real"'
        result = parser.extract(msg)
        assert result is not None
        assert result.appcat == "Real"

    def test_fields_in_different_order(self):
        parser = FortiOSParser()
        msg = 'user="carol" appcat="Music" app="Spotify"'
        result = parser.extract(msg)
        assert result is not None
        assert result.app == "Spotify"
        assert result.user == "carol"
        assert result.appcat == "Music"


class TestBaseActivityParser:
    def test_extract_not_implemented(self):
        class IncompleteParser(BaseActivityParser):
            pass

        parser = IncompleteParser()
        with pytest.raises(NotImplementedError):
            parser.extract("test")


class TestGetActivityParser:
    def test_fortios(self):
        parser = get_activity_parser("fortios")
        assert isinstance(parser, FortiOSParser)

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown activity parser"):
            get_activity_parser("nonexistent")
