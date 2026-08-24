-- Django creates a throwaway `test_<dbname>` database when running the test
-- suite, but the application user only owns the app database, so the create
-- fails with "Access denied ... to database 'test_taskmanager'".
--
-- Grant the app user rights over test_* databases only. The escaped underscore
-- (`test\_%`) matches a literal "test_" prefix; an unescaped one would treat _
-- as a single-character wildcard. This also covers the numbered databases
-- Django creates for parallel test runs (test_taskmanager_1, _2, ...).
GRANT ALL PRIVILEGES ON `test\_%`.* TO 'taskmanager'@'%';
FLUSH PRIVILEGES;
