"""Repository-owned, hash-verified text skills; no execution or Agent integration."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

ROOT = Path(__file__).resolve().parents[2]
ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
SHA256 = re.compile(r"[0-9a-f]{64}")
TOOLS = frozenset({"metric_query_service", "catalog_exact", "rag_retrieval"})
FIELDS = {"skill_id", "name", "version", "description", "source", "license", "upstream_commit",
          "instructions", "enabled", "review_status", "resources", "allowed_tools"}


class SkillError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def require(condition, code="invalid_skill_metadata"):
    if not condition:
        raise SkillError(code)


def text(value, limit):
    return isinstance(value, str) and 0 < len(value.strip()) <= limit


class SkillRegistry:
    """The root is trusted application configuration, never an HTTP/user argument.

    Only the fixed skills/registry.json is read at startup. Hashes establish consistency
    with that reviewed manifest, not authenticity against an attacker who can edit Git.
    """
    def __init__(self, repo_root=ROOT):
        root = Path(repo_root).absolute()
        require(not root.is_symlink(), "unsafe_skill_path")
        self.root = root.resolve(strict=True)
        try:
            registry = json.loads(self._read("skills/registry.json", 65536))
            require(isinstance(registry, dict) and set(registry) == {"version", "skills"})
            require(type(registry["version"]) is int and registry["version"] == 1)
            require(isinstance(registry["skills"], list) and len(registry["skills"]) <= 32)
            self._skills = {}
            for item in registry["skills"]:
                self._validate(item)
                require(item["skill_id"] not in self._skills)
                self._skills[item["skill_id"]] = deepcopy(item)
        except (ValueError, TypeError, KeyError) as error:
            if isinstance(error, SkillError):
                raise
            raise SkillError("invalid_skill_metadata") from None

    def _read(self, path, limit):
        """Walk pinned directory descriptors without following any symlink, even on swap."""
        require(isinstance(path, str) and "\\" not in path and "\x00" not in path, "unsafe_skill_path")
        parts = PurePosixPath(path).parts
        require(parts and parts[0] == "skills" and ".." not in parts
                and str(PurePosixPath(path)) == path, "unsafe_skill_path")
        fd = None
        try:
            fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            for index, part in enumerate(parts):
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                if index != len(parts) - 1:
                    flags |= os.O_DIRECTORY
                child = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = child
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_size <= limit, "invalid_skill_file")
            with os.fdopen(fd, "rb") as stream:
                fd = None
                data = stream.read(limit + 1)
            require(len(data) <= limit, "invalid_skill_file")
            return data.decode("utf-8")
        except (OSError, UnicodeError):
            raise SkillError("skill_file_unavailable") from None
        finally:
            if fd is not None:
                os.close(fd)

    @staticmethod
    def _validate(item):
        require(isinstance(item, dict) and set(item) == FIELDS)
        mid = item["skill_id"]
        require(text(mid, 64) and ID.fullmatch(mid) and item["name"] == mid)
        require(text(item["description"], 1024) and text(item["license"], 256))
        require(isinstance(item["version"], str) and re.fullmatch(r"\d+\.\d+\.\d+(?:-[a-z0-9.-]+)?", item["version"]))
        require(type(item["enabled"]) is bool and item["review_status"] in ("reviewed", "unreviewed"))
        source = item["source"]
        require(isinstance(source, dict) and set(source) == {"kind", "location"})
        require(source["kind"] in ("internal", "external") and text(source["location"], 512))
        commit = item["upstream_commit"]
        require(commit is None or isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40}", commit))
        if item["enabled"] or item["review_status"] == "reviewed":
            require(item["review_status"] == "reviewed")
            require(source["kind"] != "external" or commit is not None)
        tools = item["allowed_tools"]
        require(isinstance(tools, list) and all(isinstance(tool, str) and tool in TOOLS for tool in tools))
        require(len(set(tools)) == len(tools))
        resources = item["resources"]
        require(isinstance(resources, dict) and len(resources) <= 16)
        for name in resources:
            require(text(name, 64) and ID.fullmatch(name))
        for entry in [item["instructions"], *resources.values()]:
            require(isinstance(entry, dict) and set(entry) == {"path", "sha256"})
            path = entry["path"]
            prefix = f"skills/{mid}/"
            require(isinstance(path, str) and path.startswith(prefix) and "\\" not in path and "\x00" not in path
                    and ".." not in PurePosixPath(path).parts and str(PurePosixPath(path)) == path,
                    "unsafe_skill_path")
            digest = entry["sha256"]
            require(digest is None or isinstance(digest, str) and SHA256.fullmatch(digest))
            if item["review_status"] == "reviewed":
                require(digest is not None)
        require(item["instructions"]["path"] == f"skills/{mid}/SKILL.md", "unsafe_skill_path")

    def list_skill_summaries(self):
        return [{key: item[key] for key in ("name", "description", "version")}
                for item in self._skills.values()]

    def skill_identity(self, skill_id):
        """Trusted manifest identity for auditing, without reading or approving a body."""
        require(isinstance(skill_id, str) and skill_id in self._skills, "unknown_skill")
        item = self._skills[skill_id]
        return {"skill_id": skill_id, "version": item["version"], "sha256": item["instructions"]["sha256"]}

    def _approved(self, skill_id):
        require(isinstance(skill_id, str) and skill_id in self._skills, "unknown_skill")
        item = self._skills[skill_id]
        require(item["review_status"] == "reviewed", "skill_unreviewed")
        require(item["enabled"], "skill_disabled")
        return item

    def _verified(self, entry):
        content = self._read(entry["path"], 65536)
        require(hashlib.sha256(content.encode("utf-8")).hexdigest() == entry["sha256"], "skill_hash_mismatch")
        return content

    def load_skill_instructions(self, skill_id):
        return self._verified(self._approved(skill_id)["instructions"])

    def load_skill_resource(self, skill_id, resource_name):
        item = self._approved(skill_id)
        require(isinstance(resource_name, str) and resource_name in item["resources"], "unknown_skill_resource")
        self._verified(item["instructions"])
        return self._verified(item["resources"][resource_name])

    def select_candidates(self, question):
        """Conservative discovery only; a candidate confers no execution permission."""
        require(isinstance(question, str) and len(question) <= 2000, "invalid_skill_question")
        candidates = {}
        for clause in re.split(r"[，。；！？,;!?\n]", question):
            if re.search(r"不要|无需|不需要|不生成|别用|别生成|是否|能否|怎么|如何", clause):
                continue
            if (re.search(r"生成|导出|制作", clause) and re.search(r"excel|xlsx", clause, re.I)
                    and re.search(r"经营|业务|指标", clause) and re.search(r"报告|报表", clause)):
                candidates["xlsx"] = "explicit_excel_business_report"
            if ("分析" in clause and re.search(r"经营|指标|订单|支付|退货", clause)
                    and re.search(r"多指标|跨期|环比|趋势|排行|对比", clause)):
                candidates["business-metric-analysis"] = "explicit_complex_metric_analysis"
        return [{"skill_id": mid, "reason": reason,
                 "available": self._skills[mid]["enabled"] and self._skills[mid]["review_status"] == "reviewed"}
                for mid, reason in candidates.items() if mid in self._skills]
