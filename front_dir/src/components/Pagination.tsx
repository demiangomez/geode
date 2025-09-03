import {
    ChevronDoubleLeftIcon,
    ChevronDoubleRightIcon,
    ChevronLeftIcon,
    ChevronRightIcon,
} from "@heroicons/react/24/outline";

interface PaginationProps {
    pages: number;
    pagesToShow: number;
    activePage: number;
    handlePage: (page: number) => void;
}

const Pagination = ({
    pages,
    pagesToShow,
    activePage,
    handlePage,
}: PaginationProps) => {
    return (
        <div className="join my-4 w-full justify-center">
            <button
                type="button"
                className="join-item btn"
                onClick={() => handlePage(1)}
                disabled={activePage === 1}
            >
                <ChevronDoubleLeftIcon
                    strokeWidth={1.5}
                    stroke="currentColor"
                    className="w-6 h-6"
                />
            </button>
            <button
                type="button"
                className="join-item btn"
                onClick={() => handlePage(activePage - 1)}
                disabled={activePage === 1}
            >
                {" "}
                <ChevronLeftIcon
                    strokeWidth={1.5}
                    stroke="currentColor"
                    className="w-6 h-6"
                />
            </button>
            {Array.from({ length: pages }).map((_, i) => {
                const pageNumber = i + 1;
                if (
                    pageNumber < activePage - pagesToShow / 2 ||
                    pageNumber > activePage + pagesToShow / 2
                )
                    return null;
                return (
                    <button
                        type="button"
                        key={i}
                        className={`join-item btn ${pageNumber === activePage ? "btn-active" : ""}`}
                        onClick={() => {
                            handlePage(pageNumber);
                        }}
                    >
                        {pageNumber}
                    </button>
                );
            })}
            <button
                type="button"
                className="join-item btn"
                onClick={() => handlePage(activePage + 1)}
                disabled={activePage === pages}
            >
                <ChevronRightIcon
                    strokeWidth={1.5}
                    stroke="currentColor"
                    className="w-6 h-6"
                />
            </button>
            <button
                type="button"
                className="join-item btn"
                onClick={() => handlePage(pages)}
                disabled={activePage === pages}
            >
                <ChevronDoubleRightIcon
                    strokeWidth={1.5}
                    stroke="currentColor"
                    className="w-6 h-6"
                />
            </button>
        </div>
    );
};

export default Pagination;
