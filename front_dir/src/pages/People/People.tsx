import { PeopleTable } from "@componentsReact";

const PeopleRelations = () => {
    return (
        <div className="p-4">
            <div className="w-full text-center my-6">
                <span className="text-4xl font-bold"> People </span>
            </div>
            <div className="flex w-full justify-center">
                <PeopleTable />
            </div>
        </div>
    );
};

export default PeopleRelations;
