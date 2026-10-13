package library

import (
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"testing"
	"time"
)

// retentionCopies takes the same path as a library change: the original
// config is copied by keep into a fresh backup. Chtimes makes the absolute
// filesystem times deterministic, including on clocks with coarse precision.
func retentionCopies(t *testing.T, clocks, modified []time.Time) []string {
	t.Helper()
	sandbox(t)
	was := backupNow
	t.Cleanup(func() { backupNow = was })
	config := filepath.Join(t.TempDir(), "config.json")
	var dirs []string
	for i, at := range clocks {
		backupNow = func() time.Time { return at }
		body := fmt.Sprintf("original-%d", i)
		if err := os.WriteFile(config, []byte(body), 0o600); err != nil {
			t.Fatal(err)
		}
		b := newBackups()
		if err := b.keep("fixture-agent", config); err != nil {
			t.Fatal(err)
		}
		if got := read(t, filepath.Join(b.dir, "fixture-agent", "config.json")); got != body {
			t.Fatalf("original copy: %q, want %q", got, body)
		}
		if err := os.Chtimes(b.dir, modified[i], modified[i]); err != nil {
			t.Fatal(err)
		}
		dirs = append(dirs, b.dir)
	}
	return dirs
}

func TestBackupRetentionUsesAbsoluteTime(t *testing.T) {
	ny, err := time.LoadLocation("America/New_York")
	if err != nil {
		t.Fatal(err)
	}
	tokyo, err := time.LoadLocation("Asia/Tokyo")
	if err != nil {
		t.Fatal(err)
	}
	for name, scenario := range map[string]struct{ first, latest time.Time }{
		"DST fallback":      {time.Date(2026, 11, 1, 5, 50, 0, 0, time.UTC).In(ny), time.Date(2026, 11, 1, 6, 5, 0, 0, time.UTC).In(ny)},
		"time zone changed": {time.Date(2026, 10, 13, 10, 0, 0, 0, time.UTC).In(tokyo), time.Date(2026, 10, 13, 12, 0, 0, 0, time.UTC).In(ny)},
	} {
		t.Run(name, func(t *testing.T) {
			var clocks []time.Time
			for i := range keepBackups {
				clocks = append(clocks, scenario.first.Add(time.Duration(i)*10*time.Second))
			}
			clocks = append(clocks, scenario.latest)
			dirs := retentionCopies(t, clocks, clocks)
			if filepath.Base(dirs[len(dirs)-1]) >= filepath.Base(dirs[0]) {
				t.Fatal("fixture did not move the visible local clock backwards")
			}
			pruneBackups()
			for i, dir := range dirs {
				if i == 0 {
					if _, err := os.Stat(dir); !os.IsNotExist(err) {
						t.Fatalf("oldest copy was not pruned: %v", err)
					}
				} else if got := read(t, filepath.Join(dir, "fixture-agent", "config.json")); got != fmt.Sprintf("original-%d", i) {
					t.Fatalf("copy %d lost its original: %q", i, got)
				}
			}
		})
	}
}

func TestBackupRetentionEqualTimesKeepNumericNewest(t *testing.T) {
	at := time.Date(2026, 10, 13, 12, 0, 0, 0, time.UTC)
	clocks := slices.Repeat([]time.Time{at}, keepBackups+2)
	dirs := retentionCopies(t, clocks, clocks)
	if filepath.Base(dirs[1]) != filepath.Base(dirs[0])+"-2" || filepath.Base(dirs[9]) != filepath.Base(dirs[0])+"-10" {
		t.Fatal("collision fixture lost the existing names")
	}
	pruneBackups()
	for i, dir := range dirs {
		if i < 2 {
			if _, err := os.Stat(dir); !os.IsNotExist(err) {
				t.Fatalf("older collision %d retained: %v", i+1, err)
			}
		} else if got := read(t, filepath.Join(dir, "fixture-agent", "config.json")); got != fmt.Sprintf("original-%d", i) {
			t.Fatalf("newer collision %d lost: %q", i+1, got)
		}
	}
}

func TestBackupRetentionEqualTimesAreDeterministic(t *testing.T) {
	var results [][]string
	for _, reverse := range []bool{false, true} {
		t.Run(fmt.Sprint(reverse), func(t *testing.T) {
			at := time.Date(2026, 10, 13, 12, 0, 0, 0, time.UTC)
			clocks := slices.Repeat([]time.Time{at}, keepBackups+2)
			dirs := retentionCopies(t, clocks, clocks)
			// A nonstandard legacy folder must not make the comparator cyclic
			// between the numeric -2 and -10 collision suffixes.
			if err := os.Rename(dirs[2], dirs[0]+"-15other"); err != nil {
				t.Fatal(err)
			}
			es, err := os.ReadDir(BackupDir())
			if err != nil {
				t.Fatal(err)
			}
			if reverse {
				slices.Reverse(es)
			}
			pruneBackupDirs(BackupDir(), es)
			left, err := os.ReadDir(BackupDir())
			if err != nil {
				t.Fatal(err)
			}
			if len(left) != keepBackups {
				t.Fatalf("kept %d folders", len(left))
			}
			var names []string
			for _, e := range left {
				names = append(names, e.Name())
			}
			results = append(results, names)
		})
	}
	if !reflect.DeepEqual(results[0], results[1]) {
		t.Fatalf("equal-time results depend on directory order: %v", results)
	}
}

type retentionInfoError struct{ fs.DirEntry }

func (e retentionInfoError) Info() (fs.FileInfo, error) { return nil, fs.ErrPermission }

func TestBackupRetentionUnknownTimeLeavesCopiesAlone(t *testing.T) {
	at := time.Date(2026, 10, 13, 12, 0, 0, 0, time.UTC)
	var clocks []time.Time
	for i := range keepBackups + 2 {
		clocks = append(clocks, at.Add(time.Duration(i)*time.Second))
	}
	dirs := retentionCopies(t, clocks, clocks)
	es, err := os.ReadDir(BackupDir())
	if err != nil {
		t.Fatal(err)
	}
	es[len(es)-1] = retentionInfoError{es[len(es)-1]}
	pruneBackupDirs(BackupDir(), es)
	for i, dir := range dirs {
		if got := read(t, filepath.Join(dir, "fixture-agent", "config.json")); got != fmt.Sprintf("original-%d", i) {
			t.Fatalf("unknown time discarded copy %d: %q", i, got)
		}
	}
}

func TestBackupRetentionKeepsSkillsHiddenAndFiles(t *testing.T) {
	at := time.Date(2026, 10, 13, 12, 0, 0, 0, time.UTC)
	var clocks []time.Time
	for i := range keepBackups + 2 {
		clocks = append(clocks, at.Add(time.Duration(i)*time.Second))
	}
	retentionCopies(t, clocks, clocks)
	paths := []string{
		filepath.Join(BackupDir(), "2000-01-01_00-00-00.000", "skills", "x", "SKILL.md"),
		filepath.Join(BackupDir(), "2000-01-01_00-00-00.001", "agent", "skills", "x", "SKILL.md"),
		filepath.Join(BackupDir(), ".kept", "config.json"),
		filepath.Join(BackupDir(), "notes.txt"),
	}
	for _, p := range paths {
		write(t, p, "keep")
	}
	pruneBackups()
	for _, p := range paths {
		if got := read(t, p); got != "keep" {
			t.Fatalf("protected copy changed: %s: %q", p, got)
		}
	}
	es, err := os.ReadDir(BackupDir())
	if err != nil {
		t.Fatal(err)
	}
	if len(es) != keepBackups+len(paths) {
		t.Fatalf("kept %d entries", len(es))
	}
}
