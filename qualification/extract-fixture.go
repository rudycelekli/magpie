package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"strings"
)

func main() {
	args := os.Args[1:]
	if len(args) > 0 && args[0] == "--" {
		args = args[1:]
	}
	if len(args) != 3 {
		panic("input, new fixture output and AST receipt required")
	}
	raw, err := os.ReadFile(args[0])
	if err != nil {
		panic(err)
	}
	set := token.NewFileSet()
	tree, err := parser.ParseFile(set, args[0], raw, parser.ParseComments)
	if err != nil {
		panic(err)
	}
	if tree.Name.Name != "library" {
		panic("wrong test package")
	}
	names := []string{"retentionCopies", "TestBackupRetentionUsesAbsoluteTime", "TestBackupRetentionEqualTimesKeepNumericNewest", "TestBackupRetentionKeepsSkillsHiddenAndFiles"}
	spans := map[string]string{}
	hashes := map[string]string{}
	for _, decl := range tree.Decls {
		f, ok := decl.(*ast.FuncDecl)
		if !ok {
			continue
		}
		for _, name := range names {
			if f.Name.Name == name {
				if _, duplicate := spans[name]; duplicate {
					panic("duplicate method")
				}
				span := raw[set.Position(f.Pos()).Offset:set.Position(f.End()).Offset]
				spans[name] = string(span)
				h := sha256.Sum256(span)
				hashes[name] = hex.EncodeToString(h[:])
			}
		}
	}
	if len(spans) != len(names) {
		panic("compatible method missing")
	}
	var out strings.Builder
	out.WriteString("package library\nimport (\"fmt\";\"os\";\"path/filepath\";\"slices\";\"testing\";\"time\")\n")
	for _, name := range names {
		out.WriteString(spans[name])
		out.WriteString("\n\n")
	}
	if _, err := os.Stat(args[1]); !os.IsNotExist(err) {
		panic("fixture destination is not absent")
	}
	if err := os.WriteFile(args[1], []byte(out.String()), 0600); err != nil {
		panic(err)
	}
	inputHash := sha256.Sum256(raw)
	outputHash := sha256.Sum256([]byte(out.String()))
	receipt := map[string]any{"methods": names, "literal_function_sha256": hashes, "input_sha256": hex.EncodeToString(inputHash[:]), "fixture_sha256": hex.EncodeToString(outputHash[:]), "production_changes": false}
	encoded, err := json.MarshalIndent(receipt, "", "  ")
	if err != nil {
		panic(err)
	}
	if err := os.WriteFile(args[2], encoded, 0600); err != nil {
		panic(err)
	}
	fmt.Println(string(encoded))
}
