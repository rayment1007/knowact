import "@testing-library/jest-dom/vitest";
import { beforeEach } from "vitest";
import { clearApiCache } from "@/api/cache";

beforeEach(clearApiCache);

