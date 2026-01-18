import React from "react";
import { Link } from "react-router-dom";

export default function NotFound() {
  return (
    <div className="mx-auto px-4 md:px-6 py-6">
      <div className="mx-auto my-12 md:my-16 w-full max-w-[720px] rounded-3xl ring-1 ring-gray-200 bg-white dark:bg-gray-900 dark:ring-gray-800 shadow-sm p-10 text-center grid gap-3">
        <div className="text-2xl font-semibold text-gray-900 dark:text-gray-100">Page not found</div>
        <div className="text-sm text-gray-500 dark:text-gray-400">The page you’re looking for doesn’t exist.</div>
        <div className="pt-2">
          <Link to="/" className="inline-flex items-center gap-2 rounded-full bg-gray-900 text-white px-4 py-2 text-sm shadow hover:bg-black dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white">
            Go to dashboard
          </Link>
        </div>
      </div>
    </div>
  );
}


