""" Tests of webmon/amain.py """
import os
import shutil

from webmon.amain import parse_args, DUMMY_CONFIG_YAML


CONFIG_YAML = DUMMY_CONFIG_YAML + """
websites:
    - url: "https://www.google.com"
      regex: "<title>Google</title>"
      period: 120
    - url: "https://google.com"
      regex: "<title>Google</title>"
      period: 10
"""


class Test_parse_args:  # pylint: disable=invalid-name,missing-class-docstring
    def test_website_regex_period(self, tmp_path):
        """ We expect the options to overwrite the values from the configuration file. """
        config_filename = os.path.join(tmp_path, 'config.yaml')
        with open(config_filename, 'w', encoding='utf-8') as conf_file:
            conf_file.write(CONFIG_YAML)
        _, config = parse_args([
            'main.py',
            '-v',
            '-c', config_filename,
            '-w', 'https://bing.com', '<title>Bing</title>', '60',
            '-w', 'https://google.com', '<title>Overriden</title>', '42'])
        shutil.rmtree(tmp_path)
        assert len(config['websites']) == 3  # nosec assert_used
        assert config['websites'] == [  # nosec assert_used
            {'url': 'https://www.google.com', 'regex': '<title>Google</title>', 'period': 120},
            {'url': 'https://google.com', 'regex': '<title>Overriden</title>', 'period': 42},
            {'url': 'https://bing.com', 'regex': '<title>Bing</title>', 'period': 60}]

    def test_generate_config(self, tmp_path):
        """ We want to verify that the config.yaml is correctly generated. """
        config_filename = os.path.join(tmp_path, 'config.yaml')
        args, _ = parse_args([
            'main.py',
            '-vv',
            '-g', config_filename])
        config_text = None
        with open(config_filename, 'r', encoding='utf-8') as conf_file:
            config_text = conf_file.read()
        shutil.rmtree(tmp_path)
        assert args.generate_config  # nosec assert_used
        assert config_text == DUMMY_CONFIG_YAML  # nosec assert_used
