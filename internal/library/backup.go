package library

import (
	"cmp"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yetone/magpie/internal/settings"
)

// keepBackups is how many changes' backups are kept.
const keepBackups = 30

// BackupDir is where a file is copied before magpie first changes it in a
// change: one folder a change, named by when, a folder an agent in it.
func BackupDir() string { return filepath.Join(settings.Dir(), "backups") }

// backups copies each file once, the first time a change is about to
// write it.
type backups struct {
	dir  string
	done map[string]bool
}

func newBackups() *backups {
	return &backups{done: map[string]bool{}}
}

// backupNow is the clock a backup folder is named by.
var backupNow = time.Now

// freshBackup makes a change's backup folder, one no other change has:
// named by the millisecond, with -2, -3… after it when another change
// took that millisecond's. Two changes in one millisecond (a fast
// machine's loop) would otherwise share it, the second's file kept
// under its folder's name beside the first's, and the copy at the name
// the change wrote would be the other change's.
func freshBackup() (string, error) {
	if err := os.MkdirAll(BackupDir(), 0o700); err != nil {
		return "", err
	}
	stamp := backupNow().Format("2006-01-02_15-04-05.000")
	dir := filepath.Join(BackupDir(), stamp)
	for i := 2; ; i++ {
		err := os.Mkdir(dir, 0o700)
		if err == nil {
			return dir, nil
		}
		if !errors.Is(err, fs.ErrExist) {
			return "", err
		}
		dir = filepath.Join(BackupDir(), fmt.Sprintf("%s-%d", stamp, i))
	}
}

// keep copies path aside, as the agent had it, before it is written; a
// file that isn't there has nothing to keep.
func (b *backups) keep(agent, path string) error {
	if b.done[path] {
		return nil
	}
	b.done[path] = true
	data, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		return err
	}
	if b.dir == "" {
		dir, err := freshBackup()
		if err != nil {
			return err
		}
		b.dir = dir
	}
	dst := filepath.Join(b.dir, fileName(agent), filepath.Base(path))
	if _, err := os.Stat(dst); err == nil {
		// another file by that name (each dsh profile's cordis.patch.yml):
		// kept under its folder's name
		dst = filepath.Join(b.dir, fileName(agent), filepath.Base(filepath.Dir(path)), filepath.Base(path))
	}
	if err := os.MkdirAll(filepath.Dir(dst), 0o700); err != nil {
		return err
	}
	return os.WriteFile(dst, data, 0o600)
}

// prune leaves the newest keepBackups, and every one that holds a skill
// folder set aside (a skill taken out of the library, or an agent's own
// the library's took the place of): that is the only copy of it left.
func pruneBackups() {
	dir := BackupDir()
	es, err := os.ReadDir(dir)
	if err != nil {
		return
	}
	pruneBackupDirs(dir, es)
}

// pruneBackupDirs orders the directories by their absolute modification
// times, not their local-clock names, which can go backwards at a time-zone
// change or the end of daylight saving. Unknown times leave all copies alone.
func pruneBackupDirs(dir string, es []fs.DirEntry) {
	type backup struct {
		name     string
		modified time.Time
	}
	var dirs []backup
	for _, e := range es {
		if !e.IsDir() || strings.HasPrefix(e.Name(), ".") || holdsSkills(filepath.Join(dir, e.Name())) {
			continue
		}
		info, err := e.Info()
		if err != nil {
			return
		}
		dirs = append(dirs, backup{e.Name(), info.ModTime()})
	}
	slices.SortFunc(dirs, func(a, b backup) int {
		if order := a.modified.Compare(b.modified); order != 0 {
			return order
		}
		// A coarse filesystem clock can give several changes the same time.
		// For a shared name's millisecond, -10 follows -2, not the reverse.
		as, an, aok := backupSequence(a.name)
		bs, bn, bok := backupSequence(b.name)
		if !aok {
			as = a.name
		}
		if !bok {
			bs = b.name
		}
		if order := strings.Compare(as, bs); order != 0 {
			return order
		}
		if aok && bok {
			if order := cmp.Compare(an, bn); order != 0 {
				return order
			}
		}
		return strings.Compare(a.name, b.name)
	})
	for len(dirs) > keepBackups {
		if err := os.RemoveAll(filepath.Join(dir, dirs[0].name)); err != nil {
			return
		}
		dirs = dirs[1:]
	}
}

// backupSequence reads freshBackup's collision suffix, leaving other folder
// names to their ordinary deterministic ordering when their times tie.
func backupSequence(name string) (string, uint64, bool) {
	const stampSize = len("2006-01-02_15-04-05.000")
	if len(name) < stampSize {
		return "", 0, false
	}
	stamp := name[:stampSize]
	if _, err := time.Parse("2006-01-02_15-04-05.000", stamp); err != nil {
		return "", 0, false
	}
	if len(name) == stampSize {
		return stamp, 1, true
	}
	suffix, ok := strings.CutPrefix(name[stampSize:], "-")
	if !ok {
		return "", 0, false
	}
	n, err := strconv.ParseUint(suffix, 10, 64)
	return stamp, n, err == nil && n >= 2
}

// holdsSkills is whether a backup has skill folders in it: <when>/skills,
// or <when>/<agent>/skills.
func holdsSkills(dir string) bool {
	for _, pat := range []string{"skills", "*/skills"} {
		if m, _ := filepath.Glob(filepath.Join(dir, pat)); len(m) > 0 {
			return true
		}
	}
	return false
}
